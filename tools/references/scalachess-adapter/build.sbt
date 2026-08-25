ThisBuild / scalaVersion := "3.8.4"
ThisBuild / organization := "org.crazyhouse-stockfish"
ThisBuild / version := "0.1.0"

lazy val scalachessBuildRoot = sys.props.getOrElse(
  "scalachess.build.root",
  sys.error("-Dscalachess.build.root=<absolute exact source export> is required")
)
lazy val scalachessIdentityRoot = sys.props.getOrElse(
  "scalachess.identity.root",
  sys.error("-Dscalachess.identity.root=<absolute pinned checkout> is required")
)
lazy val pinnedScalachess = ProjectRef(file(scalachessBuildRoot).toURI, "scalachess")

lazy val root = (project in file("."))
  .settings(
    name := "crazyhouse-scalachess-reference-adapter",
    Compile / run / fork := true,
    Compile / run / javaOptions += s"-Dscalachess.identity.root=$scalachessIdentityRoot",
    libraryDependencies += "org.playframework" %% "play-json" % "3.0.6",
    scalacOptions ++= Seq("-encoding", "utf-8", "-release:21", "-deprecation", "-feature")
  )
  .dependsOn(pinnedScalachess)
