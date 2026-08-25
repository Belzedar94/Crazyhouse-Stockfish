/*
  Crazyhouse-Stockfish external Fairy comparator adapter
  Copyright (C) 2026 The Crazyhouse-Stockfish contributors

  This program is free software: you can redistribute it and/or modify
  it under the terms of the GNU General Public License as published by
  the Free Software Foundation, either version 3 of the License, or
  (at your option) any later version.

  This program is distributed in the hope that it will be useful,
  but WITHOUT ANY WARRANTY; without even the implied warranty of
  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
  GNU General Public License for more details.
*/

#ifndef _WIN32
    #error "The frozen v1 comparator adapter is a Windows-only local-gate tool"
#endif

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wincrypt.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cctype>
#include <cstdint>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <map>
#include <memory>
#include <mutex>
#include <optional>
#include <set>
#include <sstream>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

namespace {

constexpr std::string_view AdapterVersion = "fairy-crazyhouse-adapter 1";
constexpr std::string_view ExpectedEngineSha256 =
  "b79071634530b99f047a51a123b79bb7e14cfc225435f5fb6c50a9411a294183";
constexpr std::uint64_t ExpectedEngineBytes = 4530945;
constexpr std::string_view ExpectedNetworkSha256 =
  "8ebf84784ad20fa33df403e60211818a7486db7cb8c3decfc86a80238d254f43";
constexpr std::uint64_t ExpectedNetworkBytes = 58534811;
constexpr std::string_view ExpectedNetworkBasename = "crazyhouse_run15rl_e190_l03.nnue";
constexpr std::string_view ProfileId = "LICHESS_CRAZYHOUSE_2026_08_12";
constexpr std::string_view ProfileSha256 =
  "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68";
constexpr std::string_view ProfileToken =
  "LICHESS_CRAZYHOUSE_2026_08_12@"
  "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68";
constexpr std::string_view NarrowVariantDeclaration =
  "option name UCI_Variant type combo default crazyhouse var chess var crazyhouse";
constexpr std::string_view ProfileDeclaration =
  "option name CrazyhouseProfile type string default "
  "LICHESS_CRAZYHOUSE_2026_08_12@"
  "d0602bc32877639f2d9a70741614882512083431b48b9f4e98a88e1067eb4d68";
constexpr std::string_view NonceDeclaration =
  "option name CrazyhouseCapabilityNonce type string default <empty>";
constexpr std::string_view EvalFileDeclaration =
  "option name CrazyhouseEvalFile type string default <empty>";
constexpr std::string_view RawUseNnueDeclaration =
  "option name Use NNUE type check default true";
constexpr std::string_view RawEvalFileDeclaration =
  "option name EvalFile type string default <empty>";
constexpr std::string_view RawVariantPrefix = "option name UCI_Variant ";
constexpr std::string_view RawErrorPrefix = "info string ERROR:";
constexpr std::string_view ClassicalMarker = "info string classical evaluation enabled";
constexpr std::chrono::seconds ProtocolDeadline{10};

std::string trim(std::string value) {
    const auto is_space = [](unsigned char c) { return std::isspace(c) != 0; };
    value.erase(value.begin(),
                std::find_if(value.begin(), value.end(), [&](char c) {
                    return !is_space(static_cast<unsigned char>(c));
                }));
    value.erase(std::find_if(value.rbegin(), value.rend(), [&](char c) {
                    return !is_space(static_cast<unsigned char>(c));
                }).base(),
                value.end());
    return value;
}

std::string lower_ascii(std::string_view value) {
    std::string result(value);
    std::transform(result.begin(), result.end(), result.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return result;
}

bool equals_ci(std::string_view lhs, std::string_view rhs) {
    return lower_ascii(lhs) == lower_ascii(rhs);
}

bool starts_with_ci(std::string_view value, std::string_view prefix) {
    return value.size() >= prefix.size()
        && equals_ci(value.substr(0, prefix.size()), prefix);
}

std::string first_token(std::string_view line) {
    const auto space = line.find_first_of(" \t");
    return lower_ascii(line.substr(0, space));
}

std::wstring utf8_to_wide(std::string_view input) {
    if (input.empty())
        return {};
    const int needed = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, input.data(),
                                            static_cast<int>(input.size()), nullptr, 0);
    if (needed <= 0)
        throw std::runtime_error("invalid UTF-8 argument");
    std::wstring output(static_cast<std::size_t>(needed), L'\0');
    if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, input.data(),
                            static_cast<int>(input.size()), output.data(), needed)
        != needed)
        throw std::runtime_error("UTF-8 conversion failed");
    return output;
}

std::string wide_to_utf8(std::wstring_view input) {
    if (input.empty())
        return {};
    const int needed = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, input.data(),
                                           static_cast<int>(input.size()), nullptr, 0,
                                           nullptr, nullptr);
    if (needed <= 0)
        throw std::runtime_error("wide-string conversion failed");
    std::string output(static_cast<std::size_t>(needed), '\0');
    if (WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, input.data(),
                           static_cast<int>(input.size()), output.data(), needed,
                           nullptr, nullptr)
        != needed)
        throw std::runtime_error("wide-string conversion failed");
    return output;
}

std::wstring full_path(std::wstring_view input) {
    const DWORD needed = GetFullPathNameW(std::wstring(input).c_str(), 0, nullptr, nullptr);
    if (needed == 0)
        throw std::runtime_error("GetFullPathNameW failed");
    std::wstring output(static_cast<std::size_t>(needed), L'\0');
    const DWORD written = GetFullPathNameW(std::wstring(input).c_str(), needed,
                                           output.data(), nullptr);
    if (written == 0 || written >= needed)
        throw std::runtime_error("GetFullPathNameW failed");
    output.resize(written);
    return output;
}

std::wstring lower_wide(std::wstring value) {
    std::transform(value.begin(), value.end(), value.begin(), [](wchar_t c) {
        return static_cast<wchar_t>(std::towlower(c));
    });
    return value;
}

std::string hex_bytes(const BYTE* bytes, DWORD count) {
    std::ostringstream out;
    out << std::hex << std::setfill('0');
    for (DWORD i = 0; i < count; ++i)
        out << std::setw(2) << static_cast<unsigned int>(bytes[i]);
    return out.str();
}

class Handle {
   public:
    Handle() = default;
    explicit Handle(HANDLE value) : value_(value) {}
    ~Handle() { reset(); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    Handle(Handle&& other) noexcept : value_(other.release()) {}
    Handle& operator=(Handle&& other) noexcept {
        if (this != &other)
            reset(other.release());
        return *this;
    }
    HANDLE get() const { return value_; }
    explicit operator bool() const {
        return value_ != nullptr && value_ != INVALID_HANDLE_VALUE;
    }
    HANDLE release() {
        HANDLE result = value_;
        value_ = nullptr;
        return result;
    }
    void reset(HANDLE value = nullptr) {
        if (*this)
            CloseHandle(value_);
        value_ = value;
    }

   private:
    HANDLE value_ = nullptr;
};

struct AuthenticatedFile {
    Handle handle;
    std::wstring path;
    std::uint64_t bytes = 0;
    std::string sha256;
};

struct FileAuthentication {
    std::optional<AuthenticatedFile> file;
    DWORD windowsError = ERROR_SUCCESS;
    std::string detail;
};

FileAuthentication authenticate_file(const std::wstring& path, bool retainLock) {
    FileAuthentication result;
    HANDLE raw = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
                             OPEN_EXISTING,
                             FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN, nullptr);
    if (raw == INVALID_HANDLE_VALUE) {
        result.windowsError = GetLastError();
        result.detail = "open_failed";
        return result;
    }
    Handle file(raw);

    LARGE_INTEGER size{};
    if (!GetFileSizeEx(file.get(), &size) || size.QuadPart < 0) {
        result.windowsError = GetLastError();
        result.detail = "size_failed";
        return result;
    }

    HCRYPTPROV providerRaw = 0;
    if (!CryptAcquireContextW(&providerRaw, nullptr, nullptr, PROV_RSA_AES,
                              CRYPT_VERIFYCONTEXT)) {
        result.windowsError = GetLastError();
        result.detail = "crypto_context_failed";
        return result;
    }
    struct ProviderGuard {
        HCRYPTPROV value;
        ~ProviderGuard() { CryptReleaseContext(value, 0); }
    } provider{providerRaw};

    HCRYPTHASH hashRaw = 0;
    if (!CryptCreateHash(provider.value, CALG_SHA_256, 0, 0, &hashRaw)) {
        result.windowsError = GetLastError();
        result.detail = "crypto_hash_failed";
        return result;
    }
    struct HashGuard {
        HCRYPTHASH value;
        ~HashGuard() { CryptDestroyHash(value); }
    } hash{hashRaw};

    std::vector<BYTE> buffer(1U << 20U);
    for (;;) {
        DWORD read = 0;
        if (!ReadFile(file.get(), buffer.data(), static_cast<DWORD>(buffer.size()),
                      &read, nullptr)) {
            result.windowsError = GetLastError();
            result.detail = "read_failed";
            return result;
        }
        if (read == 0)
            break;
        if (!CryptHashData(hash.value, buffer.data(), read, 0)) {
            result.windowsError = GetLastError();
            result.detail = "hash_update_failed";
            return result;
        }
    }

    BYTE digest[32]{};
    DWORD digestBytes = static_cast<DWORD>(sizeof(digest));
    if (!CryptGetHashParam(hash.value, HP_HASHVAL, digest, &digestBytes, 0)
        || digestBytes != sizeof(digest)) {
        result.windowsError = GetLastError();
        result.detail = "hash_finish_failed";
        return result;
    }

    LARGE_INTEGER zero{};
    if (!SetFilePointerEx(file.get(), zero, nullptr, FILE_BEGIN)) {
        result.windowsError = GetLastError();
        result.detail = "rewind_failed";
        return result;
    }

    AuthenticatedFile authenticated;
    authenticated.path = path;
    authenticated.bytes = static_cast<std::uint64_t>(size.QuadPart);
    authenticated.sha256 = hex_bytes(digest, digestBytes);
    if (retainLock)
        authenticated.handle = std::move(file);
    result.file = std::move(authenticated);
    return result;
}

bool is_missing_error(DWORD error) {
    return error == ERROR_FILE_NOT_FOUND || error == ERROR_PATH_NOT_FOUND
        || error == ERROR_INVALID_NAME;
}

std::string option_name_from_declaration(std::string_view line) {
    constexpr std::string_view prefix = "option name ";
    constexpr std::string_view delimiter = " type ";
    if (!starts_with_ci(line, prefix))
        return {};
    const auto type = lower_ascii(line).find(std::string(delimiter));
    if (type == std::string::npos || type <= prefix.size())
        return {};
    return std::string(line.substr(prefix.size(), type - prefix.size()));
}

struct SetOption {
    std::string name;
    std::string value;
    bool valid = false;
};

SetOption parse_setoption(std::string_view line) {
    constexpr std::string_view prefix = "setoption name ";
    SetOption result;
    if (!starts_with_ci(line, prefix))
        return result;
    const std::string lowered = lower_ascii(line);
    const auto valueAt = lowered.find(" value ", prefix.size());
    if (valueAt == std::string::npos) {
        result.name = trim(std::string(line.substr(prefix.size())));
        result.value.clear();
    } else {
        result.name = trim(std::string(line.substr(prefix.size(), valueAt - prefix.size())));
        result.value = trim(std::string(line.substr(valueAt + 7)));
    }
    result.valid = !result.name.empty();
    return result;
}

bool valid_nonce(std::string_view value) {
    return value.size() == 32
        && std::all_of(value.begin(), value.end(), [](unsigned char c) {
               return std::isdigit(c) != 0 || (c >= 'a' && c <= 'f');
           });
}

std::wstring quote_command_argument(const std::wstring& value) {
    std::wstring result = L"\"";
    std::size_t backslashes = 0;
    for (wchar_t c : value) {
        if (c == L'\\') {
            ++backslashes;
            continue;
        }
        if (c == L'\"') {
            result.append(backslashes * 2 + 1, L'\\');
            result.push_back(L'\"');
            backslashes = 0;
            continue;
        }
        result.append(backslashes, L'\\');
        backslashes = 0;
        result.push_back(c);
    }
    result.append(backslashes * 2, L'\\');
    result.push_back(L'\"');
    return result;
}

enum class Phase {
    AwaitingUci,
    Handshake,
    StartupReady,
    StartupProbe,
    Ready,
    ExternalReady,
    ReconfigureReady,
    ReconfigureProbe,
    SearchGate,
    SearchActive
};

class Adapter {
   public:
    Adapter(std::wstring enginePath, std::wstring networkPath)
        : enginePath_(std::move(enginePath)), requestedNetworkPath_(std::move(networkPath)) {}

    int run() {
        if (!authenticate_inputs())
            return 3;
        if (!spawn_child())
            return 4;

        stdoutThread_ = std::thread([this] { stdout_loop(); });
        stderrThread_ = std::thread([this] { stderr_loop(); });
        watchdogThread_ = std::thread([this] { watchdog_loop(); });

        bool explicitQuit = false;
        std::string line;
        while (std::getline(std::cin, line)) {
            if (!line.empty() && line.back() == '\r')
                line.pop_back();
            std::lock_guard<std::mutex> lock(mutex_);
            if (handle_input_locked(line)) {
                explicitQuit = true;
                break;
            }
        }

        return graceful_shutdown(explicitQuit ? "quit" : "eof");
    }

   private:
    bool authenticate_inputs() {
        FileAuthentication engine = authenticate_file(enginePath_, false);
        if (!engine.file) {
            startup_error(is_missing_error(engine.windowsError)
                              ? "fairy_adapter_engine_missing"
                              : "fairy_adapter_engine_open_failed");
            return false;
        }
        if (engine.file->bytes != ExpectedEngineBytes
            || engine.file->sha256 != ExpectedEngineSha256) {
            startup_error("fairy_adapter_engine_identity_mismatch");
            return false;
        }

        std::string error;
        auto network = validate_network(requestedNetworkPath_, error);
        if (!network) {
            startup_error(error);
            return false;
        }
        lockedNetwork_ = std::move(*network);
        loadedNetworkPath_ = requestedNetworkPath_;
        expectedMarker_ = marker_for(loadedNetworkPath_);
        return true;
    }

    std::optional<AuthenticatedFile> validate_network(const std::wstring& path,
                                                       std::string& error) {
        const std::wstring basename = std::filesystem::path(path).filename().wstring();
        if (lower_wide(basename) != lower_wide(utf8_to_wide(ExpectedNetworkBasename))) {
            error = "legacy_basename_mismatch";
            return std::nullopt;
        }
        FileAuthentication file = authenticate_file(path, true);
        if (!file.file) {
            error = is_missing_error(file.windowsError) ? "legacy_missing_file"
                                                        : "legacy_network_open_failed";
            return std::nullopt;
        }
        if (file.file->bytes != ExpectedNetworkBytes
            || file.file->sha256 != ExpectedNetworkSha256) {
            error = "legacy_network_identity_mismatch";
            return std::nullopt;
        }
        return std::move(file.file);
    }

    bool spawn_child() {
        SECURITY_ATTRIBUTES security{};
        security.nLength = sizeof(security);
        security.bInheritHandle = TRUE;

        HANDLE childStdinReadRaw = nullptr;
        HANDLE childStdinWriteRaw = nullptr;
        HANDLE childStdoutReadRaw = nullptr;
        HANDLE childStdoutWriteRaw = nullptr;
        HANDLE childStderrReadRaw = nullptr;
        HANDLE childStderrWriteRaw = nullptr;

        if (!CreatePipe(&childStdinReadRaw, &childStdinWriteRaw, &security, 0)
            || !CreatePipe(&childStdoutReadRaw, &childStdoutWriteRaw, &security, 0)
            || !CreatePipe(&childStderrReadRaw, &childStderrWriteRaw, &security, 0)) {
            close_if_valid(childStdinReadRaw);
            close_if_valid(childStdinWriteRaw);
            close_if_valid(childStdoutReadRaw);
            close_if_valid(childStdoutWriteRaw);
            close_if_valid(childStderrReadRaw);
            close_if_valid(childStderrWriteRaw);
            startup_error("fairy_adapter_pipe_creation_failed");
            return false;
        }

        Handle childStdinRead(childStdinReadRaw);
        childStdinWrite_.reset(childStdinWriteRaw);
        childStdoutRead_.reset(childStdoutReadRaw);
        Handle childStdoutWrite(childStdoutWriteRaw);
        childStderrRead_.reset(childStderrReadRaw);
        Handle childStderrWrite(childStderrWriteRaw);

        if (!SetHandleInformation(childStdinWrite_.get(), HANDLE_FLAG_INHERIT, 0)
            || !SetHandleInformation(childStdoutRead_.get(), HANDLE_FLAG_INHERIT, 0)
            || !SetHandleInformation(childStderrRead_.get(), HANDLE_FLAG_INHERIT, 0)) {
            startup_error("fairy_adapter_pipe_inheritance_failed");
            return false;
        }

        job_.reset(CreateJobObjectW(nullptr, nullptr));
        if (!job_) {
            startup_error("fairy_adapter_job_creation_failed");
            return false;
        }
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if (!SetInformationJobObject(job_.get(), JobObjectExtendedLimitInformation,
                                     &limits, sizeof(limits))) {
            startup_error("fairy_adapter_job_policy_failed");
            return false;
        }

        STARTUPINFOW startup{};
        startup.cb = sizeof(startup);
        startup.dwFlags = STARTF_USESTDHANDLES;
        startup.hStdInput = childStdinRead.get();
        startup.hStdOutput = childStdoutWrite.get();
        startup.hStdError = childStderrWrite.get();

        PROCESS_INFORMATION process{};
        std::wstring command = quote_command_argument(enginePath_);
        std::wstring workingDirectory = std::filesystem::path(enginePath_).parent_path().wstring();
        if (!CreateProcessW(enginePath_.c_str(), command.data(), nullptr, nullptr, TRUE,
                            CREATE_SUSPENDED | CREATE_NO_WINDOW,
                            nullptr, workingDirectory.c_str(), &startup, &process)) {
            startup_error("fairy_adapter_child_create_failed");
            return false;
        }

        childProcess_.reset(process.hProcess);
        Handle childThread(process.hThread);
        childPid_ = process.dwProcessId;
        if (!AssignProcessToJobObject(job_.get(), childProcess_.get())) {
            TerminateProcess(childProcess_.get(), 4);
            startup_error("fairy_adapter_job_assignment_failed");
            return false;
        }
        if (ResumeThread(childThread.get()) == static_cast<DWORD>(-1)) {
            TerminateJobObject(job_.get(), 4);
            startup_error("fairy_adapter_child_resume_failed");
            return false;
        }
        return true;
    }

    static void close_if_valid(HANDLE handle) {
        if (handle != nullptr && handle != INVALID_HANDLE_VALUE)
            CloseHandle(handle);
    }

    void startup_error(std::string_view code) {
        std::cout << "info string ERROR startup code=" << code << '\n';
        std::cout.flush();
    }

    void emit_locked(std::string_view line) {
        std::cout << line << '\n';
        std::cout.flush();
    }

    bool write_child_locked(std::string_view line) {
        if (!childStdinWrite_)
            return false;
        std::string wire(line);
        wire.push_back('\n');
        std::size_t offset = 0;
        while (offset < wire.size()) {
            DWORD written = 0;
            const DWORD chunk = static_cast<DWORD>(
              std::min<std::size_t>(wire.size() - offset, 0x7fffffffU));
            if (!WriteFile(childStdinWrite_.get(), wire.data() + offset, chunk,
                           &written, nullptr)
                || written == 0)
                return false;
            offset += written;
        }
        return true;
    }

    [[noreturn]] void fail_locked(std::string_view stage, std::string_view code) {
        if (!failed_.exchange(true)) {
            std::ostringstream line;
            line << "info string ERROR " << stage << " code=" << code;
            emit_locked(line.str());
            if (job_)
                TerminateJobObject(job_.get(), 20);
        }
        FlushFileBuffers(GetStdHandle(STD_OUTPUT_HANDLE));
        ExitProcess(20);
    }

    void set_phase_locked(Phase phase) {
        phase_ = phase;
        phaseStarted_ = std::chrono::steady_clock::now();
    }

    bool handle_input_locked(const std::string& rawLine) {
        const std::string line = trim(rawLine);
        const std::string command = first_token(line);

        if (command == "quit") {
            normalShutdown_ = true;
            if (!write_child_locked("quit"))
                fail_locked("shutdown", "fairy_adapter_child_write_failed");
            return true;
        }

        if (phase_ == Phase::AwaitingUci) {
            if (command != "uci")
                fail_locked("startup", "fairy_adapter_expected_uci");
            handshakeLines_.clear();
            set_phase_locked(Phase::Handshake);
            if (!write_child_locked(line))
                fail_locked("startup", "fairy_adapter_child_write_failed");
            return false;
        }

        if (phase_ == Phase::Handshake || phase_ == Phase::StartupReady
            || phase_ == Phase::StartupProbe) {
            fail_locked("startup", "fairy_adapter_command_before_uciok");
        }

        if (command == "uci")
            fail_locked("protocol", "fairy_adapter_duplicate_uci");

        if (command == "setoption") {
            if (phase_ != Phase::Ready)
                fail_locked("setoption", "fairy_adapter_setoption_while_busy");
            handle_setoption_locked(line);
            return false;
        }

        if (command == "isready") {
            if (phase_ != Phase::Ready)
                fail_locked("isready", "fairy_adapter_isready_while_busy");
            begin_external_ready_locked();
            return false;
        }

        if (command == "go") {
            if (phase_ != Phase::Ready)
                fail_locked("go", "fairy_adapter_go_while_busy");
            if (currentVariant_ != "crazyhouse")
                fail_locked("go", "fairy_adapter_non_crazyhouse_search");
            const std::string lowered = lower_ascii(line);
            const bool perft = lowered == "go perft" || lowered.find("go perft ") == 0;
            if (!perft) {
                searchMarkerCount_ = 0;
                set_phase_locked(Phase::SearchGate);
            }
            if (!write_child_locked(line))
                fail_locked("go", "fairy_adapter_child_write_failed");
            return false;
        }

        if (command == "stop" || command == "ponderhit") {
            if (phase_ != Phase::SearchGate && phase_ != Phase::SearchActive
                && phase_ != Phase::Ready)
                fail_locked("protocol", "fairy_adapter_search_control_while_busy");
            if (!write_child_locked(line))
                fail_locked("protocol", "fairy_adapter_child_write_failed");
            return false;
        }

        static const std::set<std::string> relayCommands = {
          "debug", "register", "ucinewgame", "position"};
        if (relayCommands.count(command) != 0) {
            if (phase_ != Phase::Ready)
                fail_locked("protocol", "fairy_adapter_command_while_busy");
            if (!write_child_locked(line))
                fail_locked("protocol", "fairy_adapter_child_write_failed");
            return false;
        }

        fail_locked("protocol", "fairy_adapter_command_forbidden");
    }

    void handle_setoption_locked(const std::string& line) {
        const SetOption option = parse_setoption(line);
        if (!option.valid)
            fail_locked("setoption", "fairy_adapter_setoption_malformed");
        const std::string name = lower_ascii(option.name);

        if (name == "uci_variant") {
            const std::string value = lower_ascii(option.value);
            if (value != "crazyhouse" && value != "chess")
                fail_locked("setoption", "fairy_adapter_variant_forbidden");
            if (value != currentVariant_)
                needsProbe_ = value == "crazyhouse";
            currentVariant_ = value;
            if (!write_child_locked("setoption name UCI_Variant value " + value))
                fail_locked("setoption", "fairy_adapter_child_write_failed");
            return;
        }

        if (name == "crazyhouseprofile") {
            configuredProfile_ = option.value;
            return;
        }
        if (name == "crazyhousecapabilitynonce") {
            pendingNonce_ = option.value;
            noncePending_ = true;
            return;
        }
        if (name == "crazyhouseevalfile" || name == "evalfile") {
            if (option.value.empty() || option.value == "<empty>")
                requestedNetworkPath_ = loadedNetworkPath_;
            else {
                try {
                    requestedNetworkPath_ = full_path(utf8_to_wide(option.value));
                } catch (const std::exception&) {
                    fail_locked("setoption", "legacy_network_path_invalid");
                }
            }
            return;
        }
        if (name == "use nnue") {
            if (!equals_ci(option.value, "true"))
                fail_locked("setoption", "fairy_adapter_nnue_disabled");
            return;
        }
        if (name == "variantpath" && !option.value.empty()
            && option.value != "<empty>")
            fail_locked("setoption", "fairy_adapter_variant_path_forbidden");

        if (!write_child_locked(line))
            fail_locked("setoption", "fairy_adapter_child_write_failed");
    }

    void begin_external_ready_locked() {
        if (currentVariant_ == "chess") {
            if (noncePending_)
                fail_locked("isready", "crazyhouse_capability_nonce_wrong_variant");
            set_phase_locked(Phase::ExternalReady);
            if (!write_child_locked("isready"))
                fail_locked("isready", "fairy_adapter_child_write_failed");
            return;
        }

        if (configuredProfile_ != ProfileToken)
            fail_locked("isready", "crazyhouse_profile_mismatch");
        if (noncePending_ && !valid_nonce(pendingNonce_))
            fail_locked("isready", "crazyhouse_capability_nonce_invalid");

        std::string networkError;
        const bool pathChanged = lower_wide(requestedNetworkPath_)
                              != lower_wide(loadedNetworkPath_);
        if (pathChanged) {
            auto candidate = validate_network(requestedNetworkPath_, networkError);
            if (!candidate)
                fail_locked("isready", networkError);
            pendingNetwork_ = std::move(*candidate);
            pendingMarker_ = marker_for(requestedNetworkPath_);
        }

        if (pathChanged || needsProbe_) {
            const std::wstring& path = pathChanged ? requestedNetworkPath_ : loadedNetworkPath_;
            if (!write_child_locked("setoption name Use NNUE value true")
                || !write_child_locked("setoption name EvalFile value " + wide_to_utf8(path))
                || !write_child_locked("isready"))
                fail_locked("isready", "fairy_adapter_child_write_failed");
            probeMarkerCount_ = 0;
            set_phase_locked(Phase::ReconfigureReady);
            return;
        }

        set_phase_locked(Phase::ExternalReady);
        if (!write_child_locked("isready"))
            fail_locked("isready", "fairy_adapter_child_write_failed");
    }

    std::string marker_for(const std::wstring& path) const {
        return "info string NNUE evaluation using " + wide_to_utf8(path) + " enabled";
    }

    void begin_probe_locked(Phase probePhase) {
        probeMarkerCount_ = 0;
        set_phase_locked(probePhase);
        if (!write_child_locked("eval") || !write_child_locked("isready"))
            fail_locked("probe", "fairy_adapter_child_write_failed");
    }

    void finish_probe_locked(bool startup) {
        if (probeMarkerCount_ != 1)
            fail_locked("probe", "fairy_adapter_nnue_marker_missing_or_duplicate");
        if (startup) {
            publish_handshake_locked();
            set_phase_locked(Phase::Ready);
            return;
        }
        if (pendingNetwork_) {
            lockedNetwork_ = std::move(*pendingNetwork_);
            pendingNetwork_.reset();
            loadedNetworkPath_ = requestedNetworkPath_;
            expectedMarker_ = pendingMarker_;
            pendingMarker_.clear();
        }
        needsProbe_ = false;
        complete_external_ready_locked();
    }

    void complete_external_ready_locked() {
        if (currentVariant_ == "crazyhouse") {
            ++routeEpoch_;
            std::ostringstream route;
            route << "info string route_commit status=ok ruleset=crazyhouse"
                  << " profile=" << ProfileId
                  << " profile_sha256=" << ProfileSha256
                  << " epoch=" << routeEpoch_
                  << " backend=fairy-external identity=" << ExpectedNetworkSha256
                  << " evaluator=halfkav2variants";
            emit_locked(route.str());
            if (noncePending_) {
                std::ostringstream acknowledgement;
                acknowledgement
                  << "info string crazyhouse_capability_ack status=ok profile="
                  << ProfileId << " profile_sha256=" << ProfileSha256
                  << " nonce=" << pendingNonce_;
                emit_locked(acknowledgement.str());
                noncePending_ = false;
                pendingNonce_.clear();
            }
        }
        emit_locked("readyok");
        set_phase_locked(Phase::Ready);
    }

    void publish_handshake_locked() {
        for (const std::string& line : handshakeLines_) {
            if (starts_with_ci(line, RawVariantPrefix))
                emit_locked(NarrowVariantDeclaration);
            else
                emit_locked(line);
        }
        emit_locked(ProfileDeclaration);
        emit_locked(NonceDeclaration);
        emit_locked(EvalFileDeclaration);
        emit_locked("uciok");
        handshakeLines_.clear();
    }

    void validate_handshake_locked() {
        std::size_t variantCount = 0;
        std::size_t useNnueCount = 0;
        std::size_t evalFileCount = 0;
        std::size_t idNameCount = 0;
        std::set<std::string> names;
        for (const std::string& line : handshakeLines_) {
            if (starts_with_ci(line, RawVariantPrefix))
                ++variantCount;
            if (line == RawUseNnueDeclaration)
                ++useNnueCount;
            if (line == RawEvalFileDeclaration)
                ++evalFileCount;
            if (starts_with_ci(line, "id name Fairy-Stockfish"))
                ++idNameCount;
            if (starts_with_ci(line, "option name ")) {
                const std::string name = lower_ascii(option_name_from_declaration(line));
                if (name.empty() || !names.insert(name).second)
                    fail_locked("startup", "fairy_adapter_raw_inventory_duplicate");
            }
        }
        if (variantCount != 1)
            fail_locked("startup", "fairy_adapter_raw_variant_inventory");
        if (useNnueCount != 1)
            fail_locked("startup", "fairy_adapter_raw_use_nnue_inventory");
        if (evalFileCount != 1)
            fail_locked("startup", "fairy_adapter_raw_evalfile_inventory");
        if (idNameCount != 1)
            fail_locked("startup", "fairy_adapter_raw_id_inventory");
        if (names.count("crazyhouseprofile") != 0
            || names.count("crazyhousecapabilitynonce") != 0
            || names.count("crazyhouseevalfile") != 0)
            fail_locked("startup", "fairy_adapter_raw_custom_option_collision");
    }

    void on_stdout_line_locked(const std::string& line) {
        if (line.starts_with(RawErrorPrefix))
            fail_locked("child", "fairy_adapter_child_fatal_marker");
        if (line == ClassicalMarker)
            fail_locked("child", "fairy_adapter_classical_marker");

        switch (phase_) {
        case Phase::AwaitingUci:
            emit_locked(line);
            break;
        case Phase::Handshake:
            if (line == "uciok") {
                validate_handshake_locked();
                set_phase_locked(Phase::StartupReady);
                if (!write_child_locked("setoption name UCI_Variant value crazyhouse")
                    || !write_child_locked("setoption name Use NNUE value true")
                    || !write_child_locked("setoption name EvalFile value "
                                           + wide_to_utf8(loadedNetworkPath_))
                    || !write_child_locked("isready"))
                    fail_locked("startup", "fairy_adapter_child_write_failed");
            } else
                handshakeLines_.push_back(line);
            break;
        case Phase::StartupReady:
            if (line == "readyok")
                begin_probe_locked(Phase::StartupProbe);
            break;
        case Phase::StartupProbe:
            if (line == expectedMarker_)
                ++probeMarkerCount_;
            if (line == "readyok")
                finish_probe_locked(true);
            break;
        case Phase::Ready:
            emit_locked(line);
            break;
        case Phase::ExternalReady:
            if (line == "readyok")
                complete_external_ready_locked();
            else
                emit_locked(line);
            break;
        case Phase::ReconfigureReady:
            if (line == "readyok")
                begin_probe_locked(Phase::ReconfigureProbe);
            break;
        case Phase::ReconfigureProbe: {
            const std::string& marker = pendingMarker_.empty() ? expectedMarker_ : pendingMarker_;
            if (line == marker)
                ++probeMarkerCount_;
            if (line == "readyok")
                finish_probe_locked(false);
            break;
        }
        case Phase::SearchGate:
            if (line == expectedMarker_) {
                ++searchMarkerCount_;
                if (searchMarkerCount_ != 1)
                    fail_locked("go", "fairy_adapter_nnue_marker_duplicate");
                emit_locked(line);
                set_phase_locked(Phase::SearchActive);
            } else if (!line.empty())
                fail_locked("go", line.rfind("bestmove", 0) == 0
                                      ? "fairy_adapter_bestmove_before_nnue_marker"
                                      : "fairy_adapter_output_before_nnue_marker");
            break;
        case Phase::SearchActive:
            if (line == expectedMarker_)
                fail_locked("go", "fairy_adapter_nnue_marker_duplicate");
            emit_locked(line);
            if (line.rfind("bestmove", 0) == 0)
                set_phase_locked(Phase::Ready);
            break;
        }
    }

    template<typename Callback>
    static void read_lines(HANDLE pipe, Callback&& callback) {
        std::string pending;
        std::vector<char> buffer(4096);
        for (;;) {
            DWORD read = 0;
            if (!ReadFile(pipe, buffer.data(), static_cast<DWORD>(buffer.size()), &read,
                          nullptr)
                || read == 0)
                break;
            pending.append(buffer.data(), read);
            for (;;) {
                const auto newline = pending.find('\n');
                if (newline == std::string::npos)
                    break;
                std::string line = pending.substr(0, newline);
                pending.erase(0, newline + 1);
                if (!line.empty() && line.back() == '\r')
                    line.pop_back();
                callback(line);
            }
        }
        if (!pending.empty()) {
            if (pending.back() == '\r')
                pending.pop_back();
            callback(pending);
        }
    }

    void stdout_loop() {
        read_lines(childStdoutRead_.get(), [this](const std::string& line) {
            std::lock_guard<std::mutex> lock(mutex_);
            on_stdout_line_locked(line);
        });
        std::lock_guard<std::mutex> lock(mutex_);
        stdoutClosed_ = true;
        if (!normalShutdown_ && !failed_)
            fail_locked("child", "fairy_adapter_child_unexpected_exit");
    }

    void stderr_loop() {
        read_lines(childStderrRead_.get(), [this](const std::string& line) {
            if (line.empty())
                return;
            std::lock_guard<std::mutex> lock(mutex_);
            fail_locked("child", "fairy_adapter_child_stderr");
        });
    }

    bool phase_has_deadline_locked() const {
        switch (phase_) {
        case Phase::Handshake:
        case Phase::StartupReady:
        case Phase::StartupProbe:
        case Phase::ExternalReady:
        case Phase::ReconfigureReady:
        case Phase::ReconfigureProbe:
        case Phase::SearchGate:
            return true;
        case Phase::AwaitingUci:
        case Phase::Ready:
        case Phase::SearchActive:
            return false;
        }
        return false;
    }

    void watchdog_loop() {
        while (!stopThreads_) {
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            std::lock_guard<std::mutex> lock(mutex_);
            if (!normalShutdown_ && !failed_ && phase_has_deadline_locked()
                && std::chrono::steady_clock::now() - phaseStarted_ > ProtocolDeadline)
                fail_locked("watchdog", "fairy_adapter_protocol_timeout");
        }
    }

    int graceful_shutdown(std::string_view source) {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            if (!normalShutdown_) {
                normalShutdown_ = true;
                if (!write_child_locked("quit")) {
                    if (job_)
                        TerminateJobObject(job_.get(), 21);
                    return 21;
                }
            }
        }

        const DWORD wait = WaitForSingleObject(childProcess_.get(), 5000);
        int result = 0;
        if (wait != WAIT_OBJECT_0) {
            if (job_)
                TerminateJobObject(job_.get(), 22);
            result = 22;
        } else {
            DWORD childExit = 0;
            if (!GetExitCodeProcess(childProcess_.get(), &childExit) || childExit != 0)
                result = 23;
        }

        stopThreads_ = true;
        childStdinWrite_.reset();
        if (stdoutThread_.joinable())
            stdoutThread_.join();
        if (stderrThread_.joinable())
            stderrThread_.join();
        if (watchdogThread_.joinable())
            watchdogThread_.join();
        job_.reset();
        (void) source;
        return result;
    }

    std::wstring enginePath_;
    std::wstring requestedNetworkPath_;
    std::wstring loadedNetworkPath_;
    AuthenticatedFile lockedNetwork_;
    std::optional<AuthenticatedFile> pendingNetwork_;
    std::string expectedMarker_;
    std::string pendingMarker_;

    Handle job_;
    Handle childProcess_;
    Handle childStdinWrite_;
    Handle childStdoutRead_;
    Handle childStderrRead_;
    DWORD childPid_ = 0;

    std::thread stdoutThread_;
    std::thread stderrThread_;
    std::thread watchdogThread_;
    std::mutex mutex_;
    std::atomic<bool> failed_{false};
    std::atomic<bool> stopThreads_{false};
    bool normalShutdown_ = false;
    bool stdoutClosed_ = false;

    Phase phase_ = Phase::AwaitingUci;
    std::chrono::steady_clock::time_point phaseStarted_ = std::chrono::steady_clock::now();
    std::vector<std::string> handshakeLines_;
    std::string currentVariant_ = "crazyhouse";
    std::string configuredProfile_ = std::string(ProfileToken);
    std::string pendingNonce_;
    bool noncePending_ = false;
    bool needsProbe_ = false;
    std::uint64_t routeEpoch_ = 0;
    std::size_t probeMarkerCount_ = 0;
    std::size_t searchMarkerCount_ = 0;
};

struct Arguments {
    std::string engine;
    std::string network;
    bool version = false;
};

std::optional<Arguments> parse_arguments(int argc, char** argv) {
    Arguments result;
    for (int i = 1; i < argc; ++i) {
        const std::string argument = argv[i];
        if (argument == "--version") {
            result.version = true;
        } else if ((argument == "--engine" || argument == "--network") && i + 1 < argc) {
            const std::string value = argv[++i];
            if (argument == "--engine")
                result.engine = value;
            else
                result.network = value;
        } else
            return std::nullopt;
    }
    if (!result.version && (result.engine.empty() || result.network.empty()))
        return std::nullopt;
    return result;
}

}  // namespace

int main(int argc, char** argv) {
    try {
        const auto arguments = parse_arguments(argc, argv);
        if (!arguments) {
            std::cerr << "usage: fairy-crazyhouse-adapter --engine <path> --network <path>\n";
            return 2;
        }
        if (arguments->version) {
            std::cout << AdapterVersion << '\n'
                      << "engine_sha256 " << ExpectedEngineSha256 << '\n'
                      << "network_sha256 " << ExpectedNetworkSha256 << '\n';
            return 0;
        }
        Adapter adapter(full_path(utf8_to_wide(arguments->engine)),
                        full_path(utf8_to_wide(arguments->network)));
        return adapter.run();
    } catch (const std::exception& error) {
        std::cout << "info string ERROR startup code=fairy_adapter_exception detail="
                  << error.what() << '\n';
        return 2;
    }
}
