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

The panel runs in the IDE's embedded browser (JCEF).
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
- **A Java runtime with the embedded browser (JCEF).**
  Android Studio ships a runtime without it.
  The plugin says so in its tool window and offers the IDE's own way to switch: **Choose Boot Java Runtime for the IDE**, a runtime marked JCEF, restart.
  It is done once, and the same menu puts the original runtime back.

## Build

JDK 21 is needed to run Gradle; Android Studio's own (`Contents/jbr/Contents/Home`) will do.

```bash
cd intellij-plugin
./gradlew buildPlugin
```

The package is `build/distributions/code-ai-intellij-<version>.zip`.
Its version is the product's, read from `src/code_ai/version.json`.

The build compiles against Android Studio.
By default it downloads the release named in `gradle.properties` (over a gigabyte).
To use one that is already installed, create `local.properties` (kept out of git) next to this file:

```properties
ideDir=/Applications/Android Studio.app
```

The panel is bundled by `npm run build` in `vscode-extension/`, which the build runs by itself; Node.js has to be on the PATH.

### With the backend inside

A plugin built as above carries no backend.
It then looks for one in the project's `.venv`, and after that for `code-ai` on the PATH.
To ship the frozen backend inside the plugin, so that an install needs nothing else on the machine, point the build at the folder that holds it:

```bash
./gradlew buildPlugin -PbackendDir=/path/to/folder/with/code-ai
```

The folder's contents go to the plugin's `bin/`, where the plugin looks first.
One package per platform, as with the VS Code extension: the backend is most of the download.

## Install

**Settings | Plugins | gear icon | Install Plugin from Disk**, choose the zip, restart.
The tool window is **Code-AI**, on the right.

## Run it while working on it

```bash
./gradlew runIde
```

A sandbox Android Studio starts with the plugin installed, on a runtime that has JCEF.
`-PideArgs="/path/to/a/project"` opens a project straight away.
`-PjbrVersion=` leaves the sandbox on the runtime Android Studio ships, to see what a user sees before switching.

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

**The panel says the backend did not start.**
**Code-AI: Show Backend Log** opens what the backend wrote while starting.
The log is also under the IDE's log folder, in `code-ai/`.

**Something looks wrong in the panel.**
Run the IDE with `-Didea.is.internal=true` and right-click the panel: **Open DevTools**.
