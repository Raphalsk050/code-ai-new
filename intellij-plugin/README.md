# Code-AI - Android Studio plugin

Drives the Code-AI agent from Android Studio, and from any other IDE built on the IntelliJ Platform.
It is the VS Code extension's counterpart: the same chat panel, the same backend, the IDE's own editor, diff viewer and status bar around them.

## How it works

```
Panel (React)  <-- JSON -->  Plugin (Kotlin)  <-- stdio JSON-RPC -->  code-ai bridge
vscode-extension/webview     this folder                              (Python core)
```

- **The panel is the VS Code extension's webview bundle, unchanged.**
  The build takes `vscode-extension/dist/webview.js` and ships it inside the plugin, so there is one panel to write and one to test.
- **The plugin is the panel's host.**
  It is a port of `vscode-extension/src` to Kotlin, file for file, and the wire contract is still `vscode-extension/src/protocol.ts`.
- **The backend is the same `code-ai bridge`.**
  The plugin starts it in the project's folder and restarts it when it dies.

The panel runs in the IDE's embedded browser (JCEF), which Android Studio has to be given once: see Requirements.
`web/WebviewPage.kt` rebuilds around the bundle what VS Code gives a webview: `acquireVsCodeApi()`, the default stylesheet VS Code puts under every webview, and the `--vscode-*` theme variables, filled in from the IDE's theme and editor colour scheme.

| VS Code extension (`vscode-extension/src`) | Plugin (`src/main/kotlin/com/codeai/intellij`) |
| --- | --- |
| `extension.ts` (`ChatViewProvider`) | `chat/ChatHost.kt` |
| `bridge-client.ts`, `bridge-session.ts` | `bridge/BridgeClient.kt`, `bridge/BridgeSession.kt` |
| `resolve-command.ts`, `crash-policy.ts` | `bridge/ResolveCommand.kt`, `bridge/CrashPolicy.kt` |
| `editor-context.ts` | `editor/EditorContext.kt`, `editor/EditorContexts.kt` |
| `edit-plan.ts`, `edit-diff.ts` | `diff/EditPlan.kt`, `diff/EditDiffs.kt`, `diff/DiffTabs.kt` |
| `change-ledger.ts`, `session-changes.ts` | `changes/ChangeLedger.kt`, `changes/SessionChanges.kt` |
| `agent-status.ts` | `status/AgentStatus.kt`, `status/AgentStatusWidgetFactory.kt` |
| inline completions in `extension.ts` | `inline/CodeAiInlineCompletionProvider.kt` |

A change to one side of that table belongs on the other side too.
The files with no IDE import carry the same unit tests on both sides.

## What the IDE gives the agent

- The file that has the focus, its language and the selection with its line range.
- The other files open in editor tabs.
- The project's folder, as the workspace the backend works on.
- The project's files, for the composer's `@` picker.

The chip over the composer shows what will travel with the next message, and removes it on a click.
Before a message is sent the plugin saves every unsaved file, because the agent reads the disk and would otherwise work on a file the user no longer has.

## What the agent's work looks like in the IDE

- **Edits in the diff viewer.**
  While a write streams in, or waits for approval, a diff tab shows the file as it was against the proposal.
  Once written, the right side is the file itself.
- **Changes of the conversation.**
  The panel's rail lists every file the agent changed, to open, review all at once, or revert.
  The project view marks them `M`, or `A` for a file the agent created.
- **Status bar.**
  What the agent is doing and how much of its context is used, with the tool window closed.
- **Inline hints.**
  Ghost text at the caret while the backend's inline hints are on; **Code-AI: Toggle Inline Hints** flips them.

## Requirements

- Android Studio 2025.3 or later (IntelliJ Platform 253).
- **The IDE's embedded browser (JCEF).**
  Android Studio comes without it.
  The plugin's tool window says so and offers the IDE's own way to get it, which depends on the release.
  Either way it is done once.

| Android Studio | Where the browser is | What to do |
| --- | --- | --- |
| 2026.1 and later | A plugin of JetBrains' own, **Web Browser (JCEF)**, with its own Chromium inside. | **Install Web Browser (JCEF)** in the Code-AI tool window, or Settings \| Plugins \| Marketplace, then restart. |
| 2025.3 | Part of the Java runtime the IDE runs on. | **Choose Boot Java Runtime for the IDE**, a runtime marked JCEF, then restart. The same menu puts the original runtime back. |

JetBrains' own IDEs come with the browser, and need neither.

## Setup

On Linux, one script gets a machine ready to build the plugin and try it:

```bash
cd intellij-plugin
./setup.sh
```

It installs what is missing with the distribution's package manager (apt, dnf or pacman), after showing the command and asking: a Java to start the build with, Node.js, Python for the backend, and the libraries the sandbox IDE needs to open a window.
Where the distribution's Node.js is too old, as on Ubuntu 22.04, it fetches one from nodejs.org into `.tools/` for this build alone.
It installs the backend into the repository's `.venv`, writes the Android Studio it finds to `local.properties`, and builds the package once.
Run it again whenever: what is already there is left alone.
`./setup.sh --help` lists its options, `--dry-run` among them.

## Build

```bash
cd intellij-plugin
./gradlew buildPlugin
```

The package is `build/distributions/code-ai-intellij-<version>-<system>.zip`, and **it carries the backend**: installing it is all there is to do on the machine that gets it.
Its version is the product's, read from `src/code_ai/version.json`.

The backend is the VS Code extension's, made by the same scripts, so there is one package for each system, as there:

| `-Ptarget=` | Backend inside | Where it can be built |
| --- | --- | --- |
| `linux-x64` | One frozen executable (PyInstaller). | On Linux; elsewhere with Docker. |
| `win32-x64` | The same, for Windows. | On Windows; elsewhere with Docker. |
| `darwin-arm64`, `darwin-x64` | A folder with its own Python, which it unpacks the first time it runs. | Anywhere. |
| `none` | None: the plugin looks for `code-ai` in the project's `.venv`, then on the PATH. | Anywhere. |

Left out, the target is the machine's own.
Making a backend needs Python 3.11 or newer and takes a few minutes the first time; it is made again only when the Python sources change.
`-PbackendDir=/path/to/folder` ships that folder's contents instead, for a backend made elsewhere, such as the release pipeline's.

What the build needs from the machine:

- **A Java to start Gradle with.**
  The build itself runs on JDK 21, which Gradle finds or fetches (`gradle/gradle-daemon-jvm.properties`).
- **Node.js 18 or newer**, on the PATH, to bundle the panel: the build runs `npm run build` in `vscode-extension/` by itself.
  `nodeDir=/path/to/node` in `local.properties` names another.
- **Android Studio to compile against.**
  By default the build downloads the release named in `gradle.properties` (over a gigabyte).
  To use one that is installed, create `local.properties` (kept out of git) next to this file:

```properties
ideDir=/Applications/Android Studio.app
```

From 2026.1 the build also asks the Marketplace for the Web Browser (JCEF) plugin that fits the IDE, which the sandbox and the compiler need.

## Install

**Settings | Plugins | gear icon | Install Plugin from Disk**, choose the zip, restart.
The tool window is **Code-AI**, on the right.
The first time it asks for the embedded browser, and installs it from there: see Requirements.

## Run it while working on it

```bash
./gradlew runIde
```

A sandbox Android Studio starts with the plugin installed and the embedded browser it needs.
The build fetches that browser the first time: JetBrains' plugin for 2026.1 and later, the runtime that has it for 2025.3.
It goes straight to the IDE, past the questions Android Studio asks a new user.
The sandbox runs the backend being worked on, the one `install.sh` puts in the repository's `.venv`, and not a packaged one.

- `-PideArgs="/path/to/a/project"` opens a project straight away.
- `-PideDir= -PplatformVersion=2026.2.1.8` runs another release than the one installed: the build downloads it.
- `-PjbrVersion=` leaves a 2025.3 sandbox on the runtime Android Studio ships, to see what a user sees before switching.

```bash
./gradlew test
```

## Settings

**Settings | Tools | Code-AI**, or the panel's own settings screen under Editor.

- **Command** (default `code-ai`): the executable that launches the agent; the plugin appends `bridge`.
  Left at the default it resolves in this order: the backend inside the plugin, `.venv` in the project, `code-ai` on the PATH.
  To run from source: command `python`, arguments `-m code_ai`.
- **Arguments**: inserted before `bridge`, e.g. `--config /path/config.json`.
- **Show edits in the diff viewer**: off, the panel shows them itself.

**Code-AI: Restart** (the tool window's title bar, or Find Action) applies a changed command without restarting the IDE.

## Not ported yet

- The orchestrator graph editor (`*.flow.json`).
- Explain mode's hover over the selection; the explanation shows in the panel.
- Updating itself.
  The backend's updater fetches the VS Code package; the release pipeline does not build this plugin yet.

## Troubleshooting

**The tool window says it needs the embedded browser.**
See Requirements.

**"Embedded Browser is suspended", on Linux.**
The IDE says this itself when the system keeps the browser's sandbox from starting, which Ubuntu does since 24.04.
Its notification offers the two ways out: **Install Profile** (an AppArmor profile for the IDE, the one to prefer) or **Disable Sandbox**.

**The first start waits, on macOS.**
The backend that comes in the package unpacks its Python the first time it runs, which can take a minute.
What the panel asked meanwhile is answered when it is done.

**The panel says the backend did not start.**
**Code-AI: Show Backend Log** opens what the backend wrote while starting.
The log is also under the IDE's log folder, in `code-ai/`.

**Something looks wrong in the panel.**
Run the IDE with `-Didea.is.internal=true` and right-click the panel: **Open DevTools**.
