[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('timing', 'strength')]
    [string]$Mode,

    [string]$Output,

    [ValidateRange(3, 300)]
    [int]$SampleSeconds = 60,

    [ValidateRange(0.1, 100.0)]
    [double]$MaximumCpuPercent = 5.0,

    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Get-UtcText {
    [DateTime]::UtcNow.ToString('o')
}

function Get-OwnerMarker {
    param([string]$Text)

    if ([string]::IsNullOrWhiteSpace($Text)) {
        return $null
    }
    foreach ($marker in @('Atomic', 'Horde', 'Alice', 'Spell', 'OpenBench')) {
        if ($Text -match [regex]::Escape($marker)) {
            return $marker
        }
    }
    return $null
}

function Test-TimingSensitiveProcess {
    param(
        [string]$Name,
        [string]$ExecutablePath,
        [string]$CommandLine
    )

    $identityText = "{0}`n{1}" -f $Name, $ExecutablePath
    $ownerFromIdentity = Get-OwnerMarker -Text $identityText
    if ($null -ne $ownerFromIdentity) {
        $passiveNames = @(
            'bash.exe',
            'cmd.exe',
            'conhost.exe',
            'git.exe',
            'less.exe',
            'notepad.exe',
            'powershell.exe',
            'pwsh.exe',
            'ssh.exe',
            'tail.exe'
        )
        if ($passiveNames -notcontains $Name.ToLowerInvariant()) {
            return [ordered]@{
                matched = $true
                owner = $ownerFromIdentity
                reason = 'foreign_project_native_executable'
            }
        }
    }

    $ownerFromCommand = Get-OwnerMarker -Text $CommandLine
    $interpreterNames = @(
        'java.exe',
        'node.exe',
        'python.exe',
        'pythonw.exe',
        'pypy.exe',
        'pypy3.exe'
    )
    $operationalPattern = '(?i)(?:^|[\\/ _.-])(?:bench(?:mark)?|cutechess|datagen|generate|match|openbench|selfplay|solver|train(?:er|ing)?|tune|worker)(?:$|[\\/ _.-])'
    if (
        $null -ne $ownerFromCommand -and
        $interpreterNames -contains $Name.ToLowerInvariant() -and
        $CommandLine -match $operationalPattern
    ) {
        return [ordered]@{
            matched = $true
            owner = $ownerFromCommand
            reason = 'foreign_project_controller_or_worker'
        }
    }

    return [ordered]@{
        matched = $false
        owner = $null
        reason = $null
    }
}

function Get-SanitizedWorkloads {
    $rows = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine, CreationDate)
    $foreign = @()
    $crazyhouse = @()
    foreach ($row in $rows) {
        $classification = Test-TimingSensitiveProcess `
            -Name ([string]$row.Name) `
            -ExecutablePath ([string]$row.ExecutablePath) `
            -CommandLine ([string]$row.CommandLine)
        if ($classification.matched) {
            $foreign += [ordered]@{
                pid = [int]$row.ProcessId
                parent_pid = [int]$row.ParentProcessId
                name = [string]$row.Name
                executable_path = if ($null -eq $row.ExecutablePath) { $null } else { [string]$row.ExecutablePath }
                owner = $classification.owner
                detection_reason = $classification.reason
                command_line_recorded = $false
            }
        }
        $ownExecutable = if ($null -eq $row.ExecutablePath) { '' } else { [string]$row.ExecutablePath }
        if ($ownExecutable -match '(?i)D:\\Crazyhouse-Stockfish') {
            $crazyhouse += [ordered]@{
                pid = [int]$row.ProcessId
                parent_pid = [int]$row.ParentProcessId
                name = [string]$row.Name
                executable_path = if ($null -eq $row.ExecutablePath) { $null } else { [string]$row.ExecutablePath }
                command_line_recorded = $false
            }
        }
    }
    [ordered]@{
        total_processes = $rows.Count
        foreign = @($foreign | Sort-Object owner, pid)
        crazyhouse = @($crazyhouse | Sort-Object pid)
    }
}

function Get-HostShape {
    $computer = Get-CimInstance Win32_ComputerSystem
    $processors = @(Get-CimInstance Win32_Processor)
    $power = (& powercfg.exe /getactivescheme 2>&1 | Out-String).Trim()
    [ordered]@{
        computer_name = [Environment]::MachineName
        logical_processors = [int]$computer.NumberOfLogicalProcessors
        physical_cores = [int](($processors | Measure-Object -Property NumberOfCores -Sum).Sum)
        memory_bytes = [int64]$computer.TotalPhysicalMemory
        processor_names = @($processors | ForEach-Object { [string]$_.Name })
        active_power_scheme_sanitized = $power
        priority_or_affinity_changed = $false
    }
}

$scriptPath = [IO.Path]::GetFullPath($PSCommandPath)
$repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$scriptRelativePath = [IO.Path]::GetRelativePath($repositoryRoot, $scriptPath).Replace('\', '/')
$scriptFile = Get-Item -LiteralPath $scriptPath
$producer = [ordered]@{
    path = $scriptRelativePath
    bytes = [int64]$scriptFile.Length
    sha256 = (Get-FileHash -LiteralPath $scriptPath -Algorithm SHA256).Hash.ToLowerInvariant()
}

$captured = Get-UtcText
$before = Get-SanitizedWorkloads
$hostShape = Get-HostShape
$preflight = [ordered]@{
    schema = if ($Mode -eq 'timing') { 'crazyhouse-host-timing-attestation/v1' } else { 'crazyhouse-host-strength-attestation/v1' }
    captured_utc = $captured
    mode = $Mode
    dry_run = [bool]$DryRun
    owner_task = '019ff608-f6fe-7792-b0c9-fa6d8be8e6d8'
    producer = $producer
    host = $hostShape
    process_snapshot_before = $before
    foreign_processes_mutated = $false
    command_lines_recorded = $false
    requested_sample_seconds = $SampleSeconds
    maximum_cpu_percent = $MaximumCpuPercent
}

if ($before.foreign.Count -ne 0 -or $before.crazyhouse.Count -ne 0) {
    $preflight.result = 'NOT_READY_ACTIVE_OWNED_OR_FOREIGN_WORKLOADS'
    $preflight.cpu_samples = @()
    $preflight.valid_until_utc = $null
    $json = $preflight | ConvertTo-Json -Depth 8
    [Console]::Out.WriteLine($json)
    exit 2
}

if ($DryRun) {
    $preflight.result = 'DRY_RUN_READY_FOR_CPU_SAMPLING'
    $preflight.cpu_samples = @()
    $preflight.valid_until_utc = $null
    [Console]::Out.WriteLine(($preflight | ConvertTo-Json -Depth 8))
    exit 0
}

if ([string]::IsNullOrWhiteSpace($Output)) {
    throw 'Formal attestation requires -Output'
}
$outputPath = [IO.Path]::GetFullPath($Output)
if ([IO.File]::Exists($outputPath)) {
    throw "Refusing to replace existing output: $outputPath"
}
$parent = [IO.Path]::GetDirectoryName($outputPath)
if (-not [IO.Directory]::Exists($parent)) {
    throw "Output parent does not exist: $parent"
}

$counter = Get-Counter '\Processor(_Total)\% Processor Time' -SampleInterval 1 -MaxSamples $SampleSeconds
$samples = @($counter.CounterSamples | ForEach-Object { [math]::Round([double]$_.CookedValue, 6) })
$after = Get-SanitizedWorkloads
$allBelow = $samples.Count -eq $SampleSeconds -and @($samples | Where-Object { $_ -ge $MaximumCpuPercent }).Count -eq 0
$workloadsAbsent = $after.foreign.Count -eq 0 -and $after.crazyhouse.Count -eq 0

$preflight.process_snapshot_after = $after
$preflight.cpu_samples = $samples
$preflight.cpu_summary = [ordered]@{
    count = $samples.Count
    minimum = if ($samples.Count -eq 0) { $null } else { [math]::Round(($samples | Measure-Object -Minimum).Minimum, 6) }
    maximum = if ($samples.Count -eq 0) { $null } else { [math]::Round(($samples | Measure-Object -Maximum).Maximum, 6) }
    average = if ($samples.Count -eq 0) { $null } else { [math]::Round(($samples | Measure-Object -Average).Average, 6) }
    every_sample_strictly_below_limit = $allBelow
}
$preflight.result = if ($allBelow -and $workloadsAbsent) {
    if ($Mode -eq 'timing') { 'PASS_HOST_TIMING_CLEAN' } else { 'PASS_HOST_STRENGTH_READY' }
} else {
    'NOT_READY_CPU_OR_WORKLOAD_PRECONDITION'
}
$preflight.valid_until_utc = if ($preflight.result -like 'PASS_*') {
    [DateTime]::UtcNow.AddMinutes(5).ToString('o')
} else {
    $null
}
$json = $preflight | ConvertTo-Json -Depth 8
if ($preflight.result -like 'PASS_*') {
    [IO.File]::WriteAllText($outputPath, $json + "`n", [Text.UTF8Encoding]::new($false))
}
[Console]::Out.WriteLine($json)
exit $(if ($preflight.result -like 'PASS_*') { 0 } else { 2 })
