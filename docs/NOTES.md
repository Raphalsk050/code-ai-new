# NOTES

Decisions, trade-offs and loose ends. One entry per block of work, newest at
the top.

## 2026-10-16 - Comment boxes, note bubbles, and graphs the agent writes

### What was done

- **Comment boxes, as Unreal has them.** A `comment` node kind that never runs: the compiler accepts it, warns about a malformed one and leaves it out of the program. In the editor it is drawn behind everything, dragged by its title bar and resized from any edge or corner. Dragging it carries every node wholly inside it, unless it is set to move alone. C wraps the selection, double-click or F2 edits the title in place, and zoomed out the title grows to stay readable.
- **Note bubbles on any node** (`note`, `note_shown`), from the node's menu, F2, the bubble itself or the inspector. A hidden note leaves a mark that brings it back.
- **Graph tools for the agent**, a deferred `graphs` group: `graph_reference`, `write_graph`, `edit_graph`. The spec names nodes by ids the agent picks and wires them as text; edge kinds, dynamic pin counts, positions and comment sizes are derived (`core/graph/authoring.py`).

### Decisions

- **A comment is a node, not a new top-level list**, as in Unreal: selection, copy, paste, undo and deletion already work on nodes. The schema version stays 1, because the extension ships its own backend and the two cannot disagree.
- **The comment body lets clicks through.** A marquee or a right-click inside a comment is about its nodes; only the title bar and the edges belong to the box. A marquee selects a comment only when it encloses all of it.
- **What a comment carries is worked out when the drag starts**: the nodes wholly inside, nested comments included. Partly inside does not count, so a node can sit on the border without being dragged along.
- **The compiler stays the only judge.** The tools refuse only what they cannot turn into a document (an unknown kind, a missing pin or node, a second wire on an exec output); everything else goes through `compile_graph`, with the professions and tools the editor validates against, and a graph with errors is not written.
- **Specs and edits travel as JSON strings**, as the slides and PDF tools do: strict function calling would otherwise demand every nested field be declared.
- **write_graph will not overwrite** without `replace`; edit_graph is what changes a graph, because it keeps the user's layout and moves only what it adds.
- **The layout is Blueprint-like**: the exec flow left to right from Start, branches one row down, and what feeds a node data sits under it, chains growing leftwards. A comment around noted nodes leaves room for their bubbles.

### Checked

Backend suite (only the four known environment failures), 368 webview and host unit tests, 211 end-to-end specs; `e2e/notes.e2e.mjs` drives comments and bubbles with the mouse and keyboard, and opens a graph `write_graph` made (`e2e/fixtures/agent-written.flow.json`, kept current by `tests/unit/test_graph_authoring.py`) to check nothing is drawn over anything.

### Loose ends

- The comment box has no "show bubble when zoomed" or per-comment font picker beyond a number; the title already grows when zoomed out.
- Graph agents can be handed the graph tools like any other; nothing yet runs a graph from the chat.

## 2026-10-15 - 0.44.0: no clock on a long task, the chat in its own window

### What was done

- **Long tasks died at about five minutes.** Three clocks did it: a model request silent for 180 s was dropped and asked again from scratch; twenty failed attempts, which a backoff capped at 20 s spends in about five minutes, ended the turn; and `startPlan` ran the whole plan execution inside its request, holding the bridge's read loop (approvals and stop included) until the host gave up at 300 s. Now there is no silence cap by default, an unreachable model is retried until it answers or the user stops the turn, and `startPlan` starts a background turn. Host requests that wait on the model have no timeout; `dispatch_agent` is exempt from the tool wall clock; the review tool lost its 180 s.
- **config.json froze the defaults of its day.** Every save wrote the whole configuration back, so a default changed in a release never reached anyone who had saved a setting, the old limits above among them. A save now writes only what differs from the defaults (plus `api_key`, `base_url`, `model`, `workspace`) and stamps `config_version: 2`; an older file is migrated once on load.
- **Chat Completions is the default API mode**, and the migration switches `responses` to it.
- **The chat pops out into a window of its own** (VS Code 1.85+; a tab before), for another monitor. It moves rather than being copied: the sidebar says where it went and calls it back, closing the window hands everything back, and a window left open comes back after a reload.
- **The reasoning box keeps following** once it reaches its height; **stopping a turn withdraws the approval it was waiting on**, card and diff included; **sub-agent models get a List models button**.

### Decisions

- **Silence is not failure.** A thinking model may send nothing for minutes, and a retry starts the thinking over. A dead connection is found by TCP keepalive (idle 30 s, six probes 10 s apart) instead of a read cap. `max_model_call_s` still applies when set.
- **An outage is waited out, a refusal is not.** The providers now raise `TransientProviderError` for connection failures, dropped streams, 5xx and 429; everything else keeps the bound of 20. A read that went silent past a cap the user set counts as a refusal, so a model that always thinks longer than the cap does not retry for ever. `max_model_outage_s` bounds the wait for headless runs.
- **The migration drops a budget value only when it equals a past default** for that key. A user who chose exactly that value loses it once; the alternative was leaving every frozen 180 s in place.
- **`responses` is switched once, not for ever.** After the migration the file says `config_version: 2`, and a Responses mode chosen then is kept.
- **Provider fields stay in the file even at their defaults**: a changed default must not move `base_url` or `model` under a working setup.
- **The popout moves the one surface rather than mirroring it**: two live surfaces would mean two transcripts racing over one backend.

### Checked

Backend suite: everything but the four environment failures known before this batch (the two terminal UI specs that read default values out of config.json were moved to reading them back through the loader), 354 webview and host unit tests, 199 end-to-end specs, the packaging tests. New: `tests/unit/test_long_tasks.py`, the migration tests in `test_config.py`, `e2e/surface.e2e.mjs`.

### Loose ends

- **The IntelliJ changes were not built here** (no JetBrains repository reachable). Its tool window already floats natively, so it needed no popout.
- **Graph nodes keep their wall clocks**: a node's model call is bounded by its `timeout_s`, or `subagent_worker_timeout_s` (1800 s) when it sets none.
- **A model that stops mid-stream and stays silent is not announced**: the waiting notice covers the time before its first token only.
- `scripts/test-package.mjs` had a spec that failed on any Linux machine (it smoke-tested a fake binary); fixed on the way.

## 2026-10-14 - No caps on a working turn; a long conversation stays fast

### What was done

- **The delivery check at the end of a turn is gone**, and so is the `complete_task` evidence gate: a prose answer ends the turn, and `complete_task` accepts a well-formed claim.
- **No step, tool-call or turn-time cap by default** (0 is no cap; a value in config.json still applies). The stall detector, raised from 4 to 12 rounds, and the user's stop end a run that goes nowhere. Commands time out at 600 s by default.
  Sub-agents get no cap either; the coordinator stops one only after 600 s (explorers) or 1800 s (workers) without an event from it.
- **A stopped turn says why**: `turn.budget_exhausted` names the setting and its value, `turn.no_answer` marks an empty answer, and the panel and the terminal show both.
- **The settings screen that never loaded**: the VS Code client counted a request's 20 s from when it was written, so a backend slow to start (the company's file encryption tool scanning what the frozen backend unpacks) lost the answer. Time now counts from the backend's first word, up to five minutes, as the IntelliJ client already did.
- **The avatar blends between its loops and its acts** instead of jumping (`webview/avatar-blend.ts`).
- **A long conversation no longer slows the panel or the editor.** Off-screen messages are neither laid out nor painted, no layout is forced per render, the avatar loops hold still when nobody is looking, conversations are kept by the host one file each, and a long one is drawn from the newest turns.
- **Pictures in Markdown**: the panel shows workspace images named in answers and documents, and `read_file` on a Markdown file attaches the images its returned lines reference.

### Decisions

- **The single-file backend stays.** Shipping it as a folder would stop it unpacking itself on every start, the cost the encryption tool multiplies; the start allowance covers the symptom instead.
- **The avatar loops pause while the panel is hidden, or while focus is elsewhere with no turn running.** With the panel focused they run, at about 10 ms a frame at 500 turns in headless software rendering; with them still, a frame costs under 1 ms.
- **Graph nodes and imported agent definitions keep the step budgets they are given**: those are limits someone set on purpose.
- **Remote images in Markdown are not fetched** by a file read.

### Checked

500-turn conversation, production bundle in headless Chromium, before -> after: idle frame 83.8 -> 10.3 ms, layers 2,213 -> 41, keystroke p95 280 -> 72 ms, main thread per streamed piece 125 -> 17.6 ms, long tasks while streaming 2,357 -> 11, a history save 2.2 MB -> 8.5 KB. `e2e/perf.e2e.mjs` guards each of these on 300 turns.

### Loose ends

- **No test runs in CI**: the pipeline only builds packages. The e2e suite, the performance specs with it, runs with `npm test`.
- **The IntelliJ changes were not built here** (the JetBrains repository is not reachable from where they were written). `TranscriptStore.kt` was compiled and exercised against stand-ins for the IDE classes; the wiring in `ChatHost.kt` and `WebviewPanel.kt` was not.
- **Opening a long conversation still renders all of its Markdown**, a slice per frame: highlight.js guessing the language of unlabelled fences and DOMPurify are most of it.

## 2026-10-05 - The Android Studio package carries its backend

### What was done

- **Installing the package is all there is to do.**
  `./gradlew buildPlugin` now makes the backend for the target system and puts it in the plugin's `bin/`, where the plugin looks first.
  It is the VS Code extension's backend, asked of the extension's own scripts by `intellij-plugin/scripts/stage-backend.mjs`: the frozen executable for Linux and Windows, the folder with its own Python for macOS.
  One package for each system, named for it; `-Ptarget=none` builds one without a backend, `-PbackendDir` ships one made elsewhere.
- **A backend that is still starting is waited for.**
  The macOS backend unpacks its Python the first time it runs.
  What is asked of a bridge that has said nothing yet now counts its time from the bridge's first word, for up to five minutes, and what the backend's launcher says meanwhile goes to the panel.
- **A setup script for a Linux machine**, `intellij-plugin/setup.sh`: the system packages by apt, dnf or pacman, a Node.js of the build's own where the distribution's is too old, the backend in `.venv`, the installed Android Studio in `local.properties`, and the first build.
- **The build runs on JDK 21 whichever Java starts it** (`gradle/gradle-daemon-jvm.properties`), and bundles the panel with the Node.js `nodeDir` names when there is one.
- **Two faults in how the build ran npm**, found on the way.
  A variable set on the task while the build was configured made the configuration cache keep the whole environment, PATH included, as it was that day.
  And the install was skipped whenever `node_modules` existed, after one that was cut short as after a pull that changed the lock file.

### Decisions

- **Made when the package is built, not fetched when it is first run.**
  As with the extension: nothing to download on the user's machine, no Python asked of it, and a package that works without a network.
  The cost is one package for each system, most of it backend.
- **The sandbox IDE gets no packaged backend.**
  It runs the one being worked on, from the repository's `.venv`; freezing one for every run would turn seconds into minutes.
- **The plugin waits for a slow start, and the extension does not.**
  The extension's Mac installer unpacks the Python when it installs; a plugin has no installer, so its client waits instead.
  The two sides differ here on purpose.
- **The distribution's packages first, and nodejs.org only where they will not do.**
  A setup script that adds a package source to the system would outlive the build it was run for.

### Checked

- 46 unit tests, three of them new, for the wait.
- **macOS, for real.**
  The package built on this machine, unpacked into an Android Studio 2026.2.1 sandbox with no other backend to be found: the one inside unpacked its Python and answered a question about the selection.
- **The setup script, mostly simulated.**
  Its syntax under dash, and its decisions against stand-ins for apt, dnf and pacman: a bare Ubuntu 24.04, an Ubuntu 22.04 (Node.js 12, Python 3.10), a Debian 12 without JDK 21, Fedora with and without it, Arch, an unknown system, no terminal to ask, nothing missing, an old Android Studio and none.
  Run for real on this Mac where that is possible: the Node.js fetch, checked against the published sums, and the panel's `npm ci` and bundle with only that Node.js in reach.
  The Linux archive was fetched, checked and unpacked here as well, and could not be run.
- Not checked: the script on a Linux machine, and the Linux and Windows packages, which need Linux or Docker to make.
  Their backend is the one the extension's packages already carry.

### Loose ends

- **The pipeline builds no plugin package**; `build-binaries.yml` has the backends it would put in them.
- **On its home screen the panel says nothing while a backend unpacks**: the launcher's line shows inside a conversation only.

## 2026-10-05 - Android Studio 2026: the embedded browser is a plugin

### What happened

- **The plugin did not open in the Android Studio people have.**
  A colleague installed the package and the tool window threw `NoClassDefFoundError: com/intellij/ui/jcef/JBCefApp`.
  It had been built and run against 2025.3.2 only, the release installed on the machine it was written on.
  Google had shipped two since: 2026.1 (Quail) and 2026.2 (Rabbit).
- **Where an IDE keeps its embedded browser changed with 2026.1.**
  Up to 2025.3 the browser's classes are the IDE's and its Chromium is part of the Java runtime, which Android Studio ships without.
  In 2026.1 the Chromium moved into a plugin of JetBrains' own, **Web Browser (JCEF)** (`com.intellij.modules.jcef`): one build per system, about 150 MB, in the Marketplace, and listed as compatible with Android Studio.
  In 2026.2 the classes moved into that plugin too, and Android Studio has neither.
  JetBrains' IDEs bundle it.

### What was done

- **The plugin names Web Browser (JCEF) as an optional dependency**, which is what puts its classes in reach once it is installed.
  `web/EmbeddedBrowser.kt` tells apart an IDE with a browser, one whose browser plugin is missing, and one whose runtime has none, without naming a browser class: in an IDE without them a mention is an error wherever it is run into.
- **The tool window offers the way out for each.**
  From 2026.1, "Install Web Browser (JCEF)": the IDE's own installer, which picks the build for the system, then a restart.
  On 2025.3, the boot runtime with JCEF, as before.
- **The page's requests are answered through the browser's adapter class.**
  The handler interface gained three methods; the adapter of each release answers them with "ask the older way".
- **Read actions that 2026.2 does not deprecate**, and that 2025.3 has.
- **The build knows which release it is building against**, from the installed IDE's `product-info.json` or from `platformVersion`.
  From 2026.1 it fetches the browser plugin for the sandbox, in this machine's build; before, the runtime with JCEF that matches the installed IDE's own, read from that runtime's `release` file instead of written down.
  `./gradlew runIde -PideDir= -PplatformVersion=2026.2.1.8` runs any release.
- **A sandbox goes straight to the IDE.**
  Android Studio 2026 asks a new user two more things, a Google sign-in and whether to send usage statistics, each in a dialog that blocks the project from opening.
  The second is switched off for another reason too: its answer is written to `~/.android`, outside the sandbox, where it stands for the user's own Android Studio.

### Decisions

- **Optional, not required, and `sinceBuild` stays 253.**
  A required dependency would keep the plugin from loading on 2025.3, where there is no such plugin and the browser is the IDE's.
  Optional also lets the tool window open without it, to say how to get it.
- **A button, not an install behind the user's back.**
  It is a download of 150 MB and a restart: the user asks for it, and the IDE shows what it is about to fetch.
- **The Marketplace is asked which build fits, and its answer corrected.**
  `compatiblePlugin` finds the build line for an IDE; the Marketplace answers with any system's, so a rule swaps the suffix for this machine's.
- **Built against whichever IDE is at hand, and the default stays the oldest supported.**
  The same sources compile against 2025.3.2 and 2026.2.1 without a warning.

### Checked

- 43 unit tests, against both releases.
- In sandboxes, the panel driven through the browser's debugging port and the IDE's window painted from inside:
  - **2026.2.1 and 2026.1.4, each on the runtime it ships.**
    Without the browser plugin the tool window offers it; installed by the IDE's installer it comes for the right system; after the restart the panel opens and answers a question about the selection.
    On 2026.2.1 also an edit through approval, the diff tab from "proposed" to the file, the `M` in the project view and the status bar.
  - **The package as built against 2025.3.2, unpacked into a 2026.2.1 sandbox**, which is what an install from disk does: the same.
  - **2025.3.2** on the runtime with the browser: as before.
- Not checked: Linux and Windows, the button's own click and the IDE's dialog (the installer was called the way the button calls it, without the dialog), and "Install Plugin from Disk" itself.

### Loose ends

- **Nothing runs the plugin against a release before its users do.**
  The Plugin Verifier (`./gradlew verifyPlugin`) reports a class a release no longer has, and belongs in the pipeline once the plugin is built there.
- **On macOS a sandbox's copy of the browser plugin cannot be written over once it has run**: the system seals its helper applications.
  The build removes the copy before making a new one.
- **Helper processes outlive an IDE that is killed**, and a debugging port is one IDE's: two sandboxes started with the same port answer as one.

## 2026-10-05 - Android Studio plugin

### What was done

- **`intellij-plugin/`: the VS Code extension's counterpart for Android Studio**, and for any IDE on the IntelliJ Platform.
  Kotlin, built with Gradle and the IntelliJ Platform Gradle Plugin against Android Studio 2025.3.2.
- **The panel is not ported: it is the same bundle.**
  The build runs `npm run build` in `vscode-extension/` and ships `dist/webview.js` inside the plugin.
  `web/WebviewPage.kt` rebuilds around it what VS Code gives a webview: `acquireVsCodeApi()`, the default stylesheet VS Code puts under every webview, and the `--vscode-*` theme variables filled in from the IDE's theme and editor colour scheme (`web/ThemeVariables.kt`).
- **The host is ported, file for file.**
  `ChatHost` is `ChatViewProvider`, `BridgeSession` and `BridgeClient` keep their names, and the modules with no IDE import (`ResolveCommand`, `CrashPolicy`, `EditPlan`, `ChangeLedger`, `AgentStatus`, `EditorContext`) carry the same unit tests as their TypeScript originals.
  The wire contract is still `vscode-extension/src/protocol.ts`.
- **Native integrations:** the agent's edits in the IDE's diff viewer while they stream and after they land, the conversation's changes to open, review all at once and revert, `M`/`A` in the project view, a status bar widget, Settings | Tools | Code-AI, inline hints, and Restart, Focus Chat, Toggle Inline Hints and Show Backend Log as actions.
- **The open files are context**, in both IDEs.
  `EditorContext` gained `openFiles`: the other files open in tabs travel with the active one and its selection, and still do when a diff or a graph has the focus instead of a file.
  The chip over the composer counts them and names them on hover.

### Decisions

- **The embedded browser, not a second UI.**
  A Swing panel would be a second implementation of every screen, to keep in step for ever.
  The cost is that Android Studio ships a runtime without the browser (JCEF): the tool window says so and opens the IDE's own "Choose Boot Java Runtime" for the one-time switch.
- **A Kotlin host, not the TypeScript one in a sidecar.**
  The agent's logic is already in the Python backend; the host is a thin layer of routing and IDE calls, and a Node runtime inside the plugin would cost more than the port.
- **One thread for the host's state** (`HostThread`).
  The TypeScript host leans on Node handling one event at a time; the port queues backend events, panel messages and timers on one thread to keep that, and hops to the EDT for the IDE.
- **The plugin saves every file before a turn and refreshes the IDE's view of the disk after one.**
  The agent reads and writes the disk with its own hands; IntelliJ users rarely save by hand, and the IDE only looks at the disk when its window is focused again.
- **One preview diff tab.**
  Showing a change takes the place of the one shown before, as VS Code's preview tab does.
- **`https://code-ai.localhost` as the page's origin**, answered from the plugin's own resources.
  A real origin makes it a secure context (clipboard) and nothing is ever fetched from the network.

### Checked

- 43 unit tests, the JSON-RPC client among them against a fake bridge over real pipes.
- In a sandbox Android Studio 2025.3.2 on JetBrains Runtime 21.0.9 with JCEF, against the real backend: the panel renders in the IDE's colours, settings load, a question about the selection is answered with the selection in its context, an edit goes through approval and is written, the diff tab goes from "proposed" to the file itself, and the rail lists the change and opens its review.
  The panel was driven through the browser's debugging port and the IDE's window painted from inside the IDE, so nothing was typed on a real keyboard.

### Deferred

- **The orchestrator graph editor** (`*.flow.json`): a `FileEditorProvider` over the `flow.js` bundle, which the build already ships.
- **Explain mode's hover**: the explanation shows in the panel only.
- **Updating itself, and the release pipeline.**
  `build-binaries.yml` does not build the plugin, and the backend's updater only knows the VS Code package.
  Until both do, `/update` answers with how to install a newer package.
- **Other platforms and older IDEs.**
  Run and checked on macOS only; `sinceBuild` is 253 because that is the only build it was compiled and run against.
- **The keyboard inside the panel** (Escape, which the IDE uses to leave a tool window) was not exercised.

## 2026-10-05 - Panel fixes found on the way

- **The composer's working arc ran at an uneven pace.**
  It was a conic gradient turned by an angle from the field's centre: 5px a step along the long sides against 140px round the ends.
  It is now a dash on the field's outline (`webview/busy-ring.tsx`), moved by its offset.
  A dash given a unitless length, or a `calc()` over one, is accepted and not interpolated: every value is a plain `px`.
- **The composer's glass blurred the agent's working line.**
  Its reach above the field is for text on its way under it; at the end of the conversation nothing is.
  `--glass` (0 at the end, 1 once scrolled away) now scales that reach.
- **Segments and menu rows did not follow their containers' corners.**
  The radii went to 15px and what is drawn inside them stayed at 4px; each now takes its container's radius less the border and padding between the two edges.
- **The approval's "Open diff" broke onto two lines** in a sidebar's width.
- **A flaky spec**: `transcript.e2e` measured a box two frames into its entry animation.
  It waits for the animation now.

## 2026-10-03 - Extension UI redesign, stage 7: outside the webview, and wrap-up

### What was done

- **Status bar item** (`src/agent-status.ts`, pure and tested): context used
  in percent and what the agent is doing, for when the sidebar is closed.
  Spinner while working, bell while an approval waits, disconnect icon when
  offline; the tooltip names the running tool and the token counts; the item
  takes the warning background past the compaction threshold. Click opens
  the chat.
- **Explorer decorations:** every file the agent changed in the conversation
  carries `M` (or `A` for one it created) in the git colours, from the same
  ledger as the Changes section, cleared when the file is back to how it was
  or the conversation changes.

### State of the redesign

All seven stages are in: tokens and the i18n gate (1), header and floating
composer (2), transcript (3), rail with Changes (4), settings (5), terminal
blocks (6), status bar and decorations (7). Per stage: typecheck, i18n gate,
unit tests and the e2e suite pass (212 unit, 128 e2e at this point).

### Deferred

- **Gutter decorations** for the lines the agent changed: needs per-line
  ranges tracked across later edits by hand; the explorer letter and the
  Changes diff cover finding them.
- **MCP server cards** in the compact design (status dot, tool chips, approve
  on `warn-soft`).
- **Terminal:** "open in the VS Code terminal" and "expand over the chat";
  sending only changed blocks instead of the last 20.
- **`dist/webview.js` is 2.1 MB.** Measure time to first paint of the panel
  before deciding on code-splitting (the flow editor is the obvious split).
- `webview/mode-switch.tsx` and `webview/usage-bar.tsx` are unused since
  stage 2 and can be deleted.
- Syntax-highlight colours (`.hljs-*`) are still fixed hex (from stage 1).

## 2026-10-03 - Extension UI redesign, stage 6: terminal blocks

### What was done

- **Shell integration (backend).** `tools/terminal/shell_integration.py`
  starts a bare `bash`, `zsh`, `pwsh`, `powershell` or `cmd` with the OSC 633
  marks VS Code's terminal uses (A/B prompt, C command start, D exit code,
  E command line, P cwd): an init file for bash, a `ZDOTDIR` for zsh that
  sources the user's own files, a wrapped `prompt` plus an Enter handler for
  PowerShell, and a `PROMPT` for cmd.exe. The user's rc and profile still
  load first. Any other command (a dev server, a REPL, a shell with flags)
  starts exactly as asked.
- `ShellIntegration` takes the PTY stream, strips the marks before `pyte`
  sees them, and cuts it into command blocks: command, author, cwd, start and
  end, exit code, output (SGR colours kept, cursor control dropped, `\r`
  overwrites applied, capped at 64k chars per block, 50 blocks per session).
  A mark split across reads is reassembled.
- **Who ran it.** `send_text` / `send_enter` take `author` ("agent" by
  default); `SessionCommands.terminal_write` (what the user types in a UI)
  passes "user". A block opens on Enter when the shell is at its prompt, so
  shells that never send `C` (cmd.exe, bash 3.2) still get blocks; a later
  `C` only moves where the output starts. Pending output is drained before
  input is recorded, so a prompt still in the buffer is seen first.
- `terminal.screen.updated` now also carries `integrated`, `shell`, `cwd`,
  `running`, `running_author` and the last 20 `blocks` (output tail 4000
  chars each). The poller also fires when only the blocks changed.
- **Panel.** Bar with state dot, shell and cwd, Interrupt (only while your
  own command runs) and a "more" menu (Copy output, Refresh the screen, Send
  EOF, Clear, End the session). Blocks show the author (brand glyph for the
  agent, person for you), command, duration and result (check, `exit N`,
  spinner); success starts folded, failure and running start open, with a
  danger or accent stripe. ANSI colours map to the theme tokens. While the
  agent's command runs the input becomes "Take over" (sends Ctrl+C, focuses
  the input). Ended: "Session ended." and New session. Footer: short id and
  size. Without marks the raw screen stays, as before.
- Rail badge: `running`, `exit N` (danger, opens the section once), `live`,
  `closed`.
- Tests: `tests/unit/test_shell_integration.py` (9, one drives a real bash
  through `pexpect`), 4 new in `test_terminal_manager.py`,
  `webview/__tests__/ansi.test.ts`, `e2e/terminal.e2e.mjs` (5).

### Trade-offs

- **The default shell is unchanged** (`COMSPEC`, so cmd.exe on Windows). cmd
  gives blocks but no exit codes; PowerShell gives both. Switching the
  default to `pwsh` would change what the agent's commands run in.
- **ConPTY and OSC.** Older Windows builds can drop OSC sequences inside
  ConPTY; then there are simply no blocks and the raw screen shows.
- **Payload size.** Every update carries up to 20 blocks with 4000 chars of
  output each; a chatty command at the poller's 2 Hz moves up to ~160 KB/s
  over stdio. Sending only changed blocks would cut it; not measured yet.
- Not done: "open in the VS Code terminal" and "expand over the chat" from
  the design. The PTY lives in the backend, so a VS Code terminal would be a
  second session, not this one.

### Loose ends

- Run `pytest tests/unit/test_shell_integration.py tests/unit/test_terminal_manager.py tests/unit/test_session_commands.py`
  on Windows and Linux: the build environment had no `pexpect`, `pyte` or
  `pywinpty`, so the manager tests ran against stand-ins and the real-bash
  path was checked with the standard `pty` module instead.

## 2026-10-03 - Extension UI redesign, stage 5: settings

### What was done

- **Categories instead of one long page:** Model, Agent, Editor, Context,
  Tools and MCP, Sub-agents, Advanced. "Doctor" is gone as a name; each of its
  items moved to its subject (effort to Model, tools to Tools and MCP,
  sub-agents to their own, sampling to Advanced). Below 640px the categories
  become a scrolling tab strip and each row stacks name over control.
- **One row per setting:** name and description on the left, control on the
  right. Enums of up to four values are segmented controls, longer ones stay
  selects (reasoning effort has six). Numbers are right-aligned at 120px.
- **Search** in the header filters rows by name and description across all
  categories, grouped under their category.
- **What is unsaved:** an accent dot on the row and on its category, the
  header counts them ("3 unsaved"), and a floating bar with the composer's
  glass offers Discard and Save, saying how many changes need a restart.
  `Ctrl+S` saves. The bar is gone when nothing is pending and the header
  reads Saved / Not saved. After a save that touched a restart-only setting,
  a note offers "Restart now".
- **Model:** the provider's list appears under the field with the current
  model marked; typing still works for providers that do not list. "Test the
  connection" times a model listing and reports latency and model count.
- **API key:** "stored" with a Change button instead of an empty field.
- **Risk:** "Commands see secret variables" sits on `warn-soft` with a risk tag.
- **The extension's own settings** are now editable here:
  `code-ai.diff.openInEditor` (Editor), `code-ai.command` and `code-ai.args`
  (Advanced). They are saved through the host to the user's VS Code settings
  (`updateEditorSettings`), never to the backend.
- The restart tag reads "reopens panel".
- **Bug fixed:** `UserAvatar` reset its broken flag in an effect, which raced
  with `onError` for a fast-failing image and left a broken image on screen
  (the intermittent e2e failure). It now remembers which source failed.
- `e2e/settings-layout.e2e.mjs` (8 tests). `settings.e2e.mjs` and
  `mcp.e2e.mjs` navigate to the field's category first and read the status
  from the header instead of the button.

### Trade-offs

- **MCP server cards are unchanged.** The compact card design (status dot,
  tool chips, approve on `warn-soft`) is deferred; the cards work and are
  covered by 15 tests that would all move.
- **Prefs (default mode, analyse selection) still apply immediately**, as
  before, so they never show as unsaved.
- **Save still posts every backend field** when any one changed: the backend
  validates the set, and the existing tests pin that contract.

## 2026-10-03 - Extension UI redesign, stage 4: task rail

### What was done

- **Badges on every section header** (`sectionNotes` in `task-rail.tsx`), in
  the tone of what they report: Session `waiting for you` / `working` /
  `offline`, Plan `done/total` (or `-`), Changes the file count, Agents
  `n running` / `n failed`, Terminal `live` / `closed`. A section whose badge
  turns warn or bad opens itself once per occurrence (docked rail only; the
  strip never pops a drawer on its own).
- **Session in words.** Context meter with the auto-compaction mark, state
  as a sentence (`Running web_search`, `Waiting for you`, `Offline`),
  tokens spent, verification only after one ran, index progress while
  indexing. Model, workspace, branch, raw status, phase, planner and
  permission moved under "Technical details", collapsed.
- **Plan.** The auto/plan/act buttons are gone (the composer chip has them).
  Steps are 22px rows: check (done, muted text), spinner (current, bold),
  ring (pending), error (failed). No plan: one short line.
- **Changes (new).** Every file the agent changed in the conversation, with
  `+n -m` against the file as it was before the agent first touched it.
  Click opens that diff; hover shows revert (delete for a file the agent
  created), which the host confirms in a modal; Review all opens the
  multi-file diff (`vscode.changes`), falling back to the first file's diff
  on editors without it. Host side: `src/change-ledger.ts` (pure, tested)
  fed by `file.write.before` and write completions, wrapped by
  `src/session-changes.ts`; reset on new or loaded conversation; refreshed
  on save of a tracked file.
- **Widths.** Rail 288px by default (was 236); the chat keeps at least 320px.
  The docked layout starts at 640px of container (was 520); below it the
  icon strip with counts (Changes) and tone dots (activity), and the drawer.
- **Agents icon:** two brand glyphs, the back one faded, the dot in the
  bottom-right corner. The footer ("bridge connected", planner mode) is
  gone: the banner and the composer chips say both.
- `e2e/rail.e2e.mjs` (6 tests), `src/__tests__/change-ledger.test.ts` (2).

### Trade-offs

- **Revert writes the snapshot back whole.** Edits made by hand after the
  agent's are lost too; the confirmation says so. A per-hunk revert would
  need a merge, and the editor's own diff already allows reverting hunks.
- **Line counts are against disk at refresh time**, recomputed on every
  write and on saves of tracked files; edits not yet saved are not counted.
- **A file over the snapshot limit (512k chars) or binary** is listed without
  counts and cannot be reverted, because its original was never captured.
- **The ledger lives in the extension host** and is lost on window reload;
  persisting originals would mean storing copies of user files.

## 2026-10-03 - Extension UI redesign, stage 3: transcript

### What was done

- **Tool cards (`webview/tool-row.tsx`).** Same anatomy as before (status,
  kind, name; target and result below). Codicons; the kind icon is muted so
  accent marks only what runs. No badge on success: badges only for
  `running` and `failed`, in lower case. Duration on the right, from the
  envelope timestamps of `tool.call.started` and its end. Writes show
  `+n -m`. A failed call is open from the start, on a danger background, with
  Copy and (for commands) Open the terminal.
- **Folded runs.** Three or more consecutive successful calls collapse into
  one line ("3 tools", the names, the summed time); a click opens them.
- **Line counts from the backend.** `write_file` and `edit_code` now return
  `lines_added` / `lines_removed` (`filesystem/common.py: line_delta`). An
  overwrite of a binary or oversized file reports none. The webview falls back
  to counting the unified diff `edit_code` already returned.
- **Approval card.** Header on `warn-soft` with the kind icon and a "waiting
  for you" badge. Allow once is solid, Allow for the session outlined, Deny
  outlined in danger, each with its key inside the button; the line of key
  hints is gone. Code changes show the path, `+n -m` and the diff; when the
  editor diff is on, the first 8 lines plus "Open diff".
- **Turn footer.** `turn.completed` appends a `turn` item: wall time from the
  first `user.message` of the turn, tokens from the cumulative usage delta,
  files written, and the last `latest_verification_passed` seen in the turn.
  A turn with none of these leaves no footer. A steer mid-turn does not
  restart the clock. The footer does not stop the box from turning green.
- **Banner over the composer.** Offline (`DISCONNECTED`) with Reconnect,
  connecting (`STARTING` / `RECONNECTING`), and model retries. Retries no
  longer leave a notice in the transcript: the session keeps the attempt and
  the reason, and the banner goes when the model answers. Sending is disabled
  for all three offline states (was `STARTING` only). The header glyph turns
  red offline.
- **Empty conversation.** With a selection: Explain the selection (a prompt
  with the editor context) and Refactor the selection (refactor mode, analysis
  started). Without: Explain this workspace and Review uncommitted changes.
  Then the three most recent conversations.
- `IconAgentsTwin`: two of the brand glyph, front one ringed in the surface
  colour; used for the `agent` tool kind.
- `e2e/turns.e2e.mjs` (8 tests) and 3 new unit tests.

### Trade-offs

- **"Waiting for approval" is the approval card, not a tool row.** The
  backend emits `tool.call.started` only after the approval resolves, so a
  waiting call has no card to badge. Emitting it earlier would change what
  every front end sees; not worth it for a duplicate of the approval card.
- **Durations need envelope timestamps.** Saved conversations rebuilt from
  messages have none, so their cards and footers show no time.
- **The footer's token count is the cumulative delta**, so it includes
  compaction or sub-agent calls made inside the turn. That is what the turn
  cost, which is the point.
- **Fold threshold is 3.** Two calls are short enough to read as they are.

### Loose ends

- Python tests (`test_writes_and_edits_report_the_lines_they_changed`) could
  not run in the build environment; run `pytest tests/unit/test_filesystem_tools.py`.

## 2026-10-03 - Extension UI redesign, stage 2: header and composer

### What was done

- **Codicons.** `@vscode/codicons` is a dependency; `esbuild.mjs` copies
  `codicon.css`/`codicon.ttf` to `dist/codicons`, the webview links them (CSP
  gains `font-src`), `verify-vsix` requires them. `webview/codicon.tsx` renders
  one.
- **Header (`chat-header.tsx`).** 40px: back, brand mark, title, branch chip,
  a status glyph (spinner or dot, the activity text only in the tooltip), the
  context meter, new / history / task panel / settings. Below the narrow
  breakpoint the branch, status and history go.
- **Context meter (`context-meter.tsx`).** Always visible; unknown is drawn as
  a dashed track; past the compaction threshold it becomes the button that
  compacts. Replaces `UsageBar`.
- **Floating composer.** The composer floats over the transcript on a blurred
  glass layer; the transcript pads by `--composer-h` (ResizeObserver). A jump
  button appears when scrolled up. While the agent works an accent arc orbits
  the field (`@property` angle, static border under reduced motion).
- **Composer bar (`composer-bar.tsx`, `chip-menu.tsx`).** Mode, plan and
  permission chips on the left (they wrap), model chip and send/stop on the
  right (never wrap, so the button keeps its corner at every width). The
  model chip lists models on open and saves the pick. Refactor and explain
  modes show the same bar in a footer. Replaces the mode bar.
- **The task panel's docked state moved up to `index.tsx`** so the header
  button can toggle it.
- `e2e/chrome.e2e.mjs`: 8 tests (header parts, meter states, chips, keyboard,
  busy ring and stop, fixed send corner, glass and jump button).

### Trade-offs

- **`backdrop-filter` costs a compositing layer** over a scrolling list. It is
  one fixed-size layer, acceptable for a sidebar; no profile was taken.
- **The model chip saves through `updateSettings`**, the same path the
  settings form uses, so a model change from the chip is a settings write.
- **`button:not(:disabled)` in `motion.ts` forces `position: relative`** on
  every button (for the ripple). Absolutely positioned buttons need a more
  specific selector (`.composer-wrap > .jump-btn`).

### Loose ends

- `webview/mode-switch.tsx` and `webview/usage-bar.tsx` are no longer imported
  and should be deleted.
- `test-package.mjs` was only partly runnable here (no backend tree, no
  `vsce`); the codicon entries in `verify-vsix` need a local `npm test`.

## 2026-10-03 - Extension UI redesign, stage 1: design tokens and i18n gate

Implements the "Code-AI" design system in stages; this is
the foundation the later stages build on.

### What was done

- **Design tokens.** `webview/styles/tokens.ts` is now the only place a colour,
  font size, spacing step or radius is decided. Every colour maps to a
  `--vscode-*` variable (fallbacks are Dark Modern, used only outside VS Code).
  `base.ts`, `panels.ts` and `rail.ts` read tokens only: the duplicated
  fallbacks (`#f85149`/`#f14c4c`, four greens, two yellows) are gone, the 13
  font sizes collapse to four (11/12/body/14, body follows
  `--vscode-font-size`), radii map to `--radius-sm|md|lg|pill`.
- **The agent avatar** uses `--accent` instead of an accent-to-purple gradient:
  purple was the only colour outside the theme.
- **i18n gate.** `scripts/check-i18n.mjs` (TypeScript AST, run as
  `npm run lint:i18n` and part of `npm test`) fails on JSX text, string
  children, conditional/template strings and user-facing attributes that skip
  `t()`/`tf()`. 70 such strings were fixed; the pt-BR ones that were hard-coded
  now have an English key and a pt entry.

### Trade-offs

- **Radii follow the design system as edited (15/15/20px)**, which is rounder
  than the old 6-10px. One token change reverts it.
- **The i18n check is syntactic.** Strings built outside JSX (a label map, a
  helper returning text) are not seen; they still need review.
- **The orchestrator graph (`webview/flow/`) keeps its own stylesheet** and is
  out of scope for this redesign.

### Loose ends

- Syntax-highlight colours in `base.ts` (`.hljs-*`) are still fixed hex; they
  should map to the editor's token colours.
- `@vscode/codicons` (chosen for icons) could not be installed in the build
  environment used for this stage; it lands with stage 2.

## 2026-09-22 - Orchestrator graph, and English as the product language

### What was done

- **Orchestrator graph.** A `*.flow.json` custom editor drives a node graph
  whose Agent nodes are sub-agent profiles: `src/code_ai/core/graph/` compiles
  and validates the document, `app/graph_runner.py` runs it, and the editor in
  `vscode-extension/webview/flow/` is a hand-rolled SVG canvas. Nodes come from
  a right-click menu, Blueprint-style; dragging a wire off a pin and dropping it
  on empty canvas offers only the nodes that pin can reach and creates the node
  already connected.
- **English is the product language.** Every string the tool shows is the
  English one, including the pt-BR that predated this work (the chat panel, the
  TUI, the goal/terminal/question messages). `webview/i18n.ts` applies a pt-BR
  table only when the backend says that is the language the user picked, and the
  node catalog travels with that choice so the graph editor follows it too.

### Trade-offs

- **The i18n table is keyed by the English string**, not by a symbolic key.
  There is no key registry to keep in sync, and a missing translation falls back
  to readable English instead of to `settings.save.button`. The cost is that
  editing an English string silently drops its translation until the table
  catches up, and that two uses of one word need an explicit context
  (`t("Run", "tab")`).
- **Only the webviews switch language.** The TUI and the backend messages are
  English-only; the `language` setting still decides what language the *model*
  answers in, which is what it always meant. Making the TUI switchable would
  need the same table threaded through Textual widgets, and nothing asked for it
  yet.
- **Undo in the graph editor is snapshot-based**, not a per-command inverse. A
  whole-graph snapshot per mutation makes "undo restores exactly what was there"
  true by construction; at a few hundred nodes the memory cost is nothing.
- **A graph run occupies the session's turn slot.** The app runs one model turn
  at a time, so starting a graph while the chat is answering is refused with a
  reason rather than queued.

### Loose ends

- The Portuguese that remains in the repo is data the code matches against:
  input detectors (`core/planning/models.py`, `core/internet_intent.py`,
  `tools/documents/tools.py`), the recall stopword list
  (`core/memory_recall.py`) and the document label tables
  (`tools/documents/builder.py`, `tools/latex/markup.py`).
- Two settings-panel e2e scenarios fail on `main` and still fail: after a clean
  save the button reads "Save" instead of "Saved" and the api key field keeps
  what was typed. Unrelated to this work, filed separately.
- Parallel branches in a graph (`max_concurrent_nodes > 1`) would make the
  single travelling indicator ambiguous; the indicator needs a design pass
  first.

## 2026-09-20 - VS Code extension: parity closed, tests and e2e with screenshots

Second pass, closing what the first one left open. All of it verified with
`npm run test` (typecheck + webview unit tests + e2e in Chromium) and
`pytest tests/unit tests/integration`.

### What was done

- **Per-conversation summary.** `ConversationStore` gained `summary`,
  `summary_at` and `total_tokens`; `CodeAIApplication.summarize_conversation()`
  writes the summary with one model call and does not spend another while the
  thread has not moved (`summary_at >= updated_at`). The token total is the
  distance from the `_usage_baseline` recorded when the conversation became the
  current one, not the absolute value of the process ledger, which counts every
  other thread too. The extension asks for the summary when you leave the
  conversation (new one, open another, back to the history).
- **`@` in the composer.** Ranking and insertion in `webview/mentions.ts` (name
  beats path, shorter wins ties); the search is the host's, through
  `vscode.workspace.findFiles`, which already respects `files.exclude` and costs
  no process hop.
- **Esc switches between chat and terminal**, as in the TUI. The listener is on
  the document because the terminal input lives in the rail, which the composer
  knows nothing about; opening the right section is a `reveal` with a nonce.
- **Pasted images** become chips in the composer and travel in
  `submitUserMessage` (`_parse_images` on the bridge drops whatever arrived
  broken rather than failing the turn).
- **`/skills` and `/workflows`** are in the catalog the bridge serves, tagged by
  `kind`, and run through `runAsset`; `resolve_asset` returns the prompt and the
  caller decides how to run the turn. `/doctor`, `/status` and `/config` open the
  settings, and `/config <field> <value>` applies straight through
  `updateSettings`.
- **Doctor in the extension.** `get_settings` now exposes `disabled_tools`,
  `subagent_models`, `max_concurrent_subagents` and the sampling knobs, plus the
  tool catalog read from the live registry (not from the config: a tool the
  session never registered must not become a switch that does nothing).
  `update_settings` takes the same fields and calls `registry.set_disabled()`, so
  turning a tool off applies from the next request.
- **Approval became an inline card.** It used to be a modal: in a narrow sidebar
  the overlay covered exactly the diff, the command output and the plan - the
  context the decision comes from. Now it sits at the end of the transcript, with
  A/S/D on the keyboard, and only leaves once the bridge confirms (two clicks do
  not resolve the same call twice).
- **Tests.** 33 webview unit tests (`node scripts/test-webview.mjs`: esbuild
  bundles, node's own runner executes - no new framework to install); 40 e2e
  checks in Chromium with a screenshot of each state; on the Python side
  `SessionCommands`, the catalog, the store, and a
  `tests/integration/test_bridge_e2e.py` that drives `BridgeServer` over real
  JSON-RPC lines against a real application.

### Trade-offs

- **The e2e uses the real bundle, not VS Code.** `harness.html` fabricates what
  the editor would give the webview (the `--vscode-*` variables and
  `acquireVsCodeApi`). It covers layout, state and interaction; it does not cover
  the host or the bridge - those have the Python integration tests. Running a
  real VS Code (`@vscode/test-electron`) would cover both sides and is the
  natural next step once the host grows more logic.
- **Screenshots are not compared pixel by pixel.** They document the state and
  show regressions to the eye; an image baseline in CI would mean pinning fonts
  and rendering, which is expensive for the return at this size of project.
- **The drawer touches the icon strip (sm) and covers the sidebar (xs).** Below
  380px the two do not fit, so there the only way back is the drawer's ✕.
- **The summary costs one model call per conversation closed.** It is explicit
  and only when the thread changed; an automatic summary per turn would cost a
  lot without improving the list.

### Loose ends

- `webview/reducer.ts` is orphaned and should be deleted (`git rm`).
- The TUI's `/doctor` still has screens the extension does not (provider
  diagnosis, experiments); tools, sub-agents and sampling are already there.
- A screenshot baseline in CI, if the layout settles.
- Images only arrive by pasting; dragging a file in does not work yet.

## 2026-09-20 - VS Code extension: panel chassis, sliced store and motion

### What was done

- **Sliced store in the webview** (`webview/store/`). The single reducer became
  ten slices (`transcript`, `session`, `usage`, `approvals`, `plan`, `agents`,
  `terminal`, `questions`, `code`, `goal`). `applyEvent` calls them all and
  returns the same object when none changed, so a streaming delta repaints the
  transcript and nothing else. The old `webview/reducer.ts` was replaced by
  `webview/store/transcript.ts`.
- **Task rail** (`webview/task-rail.tsx`). Session, Plan, Agents, Terminal and
  History as sections of one component, with three layouts: docked column
  (>520px), overlaid drawer (380-520px) and full drawer (<380px). The width comes
  from a `ResizeObserver` (`use-container-width.ts`), not from a media query: the
  webview only knows its own box, not the window.
- **Parity with the TUI**: plan with steps and evidence, sub-agents with an open
  circuit, `ask_user` questions inline in the chat, live code during
  `write_file`, a context bar that is always visible, an interactive terminal,
  the objective loop, and the index with progress.
- **Shared command layer** (`app/commands.py`). Terminal, objective, plan and
  index moved out of `TerminalController` into `SessionCommands`, which returns
  data; the TUI formats its own strings and the bridge serialises the same
  result. Before this, the bridge would have had to duplicate the behaviour just
  to have `/term`.
- **Shared slash-command catalog** (`app/slash_catalog.py`). One list;
  `bridge_supports()` marks what the bridge runs today and the extension shows
  the rest greyed out instead of pretending it works.
- **New JSON-RPC methods**: `replan`, `startPlan`, `planSnapshot`,
  `goalDefine/Start/Stop/Status`, `terminalStart/Write/Control/Kill/Status`,
  `indexRefresh`, `indexStatus`, `listSlashCommands`. The ones that make a model
  call, walk the workspace or cancel the turn go into `_CONCURRENT_METHODS` -
  holding the read loop would freeze the approvals arriving on the same pipe.

### Trade-offs

- **No animation library.** Web Animations API and CSS, nothing in the bundle.
  VS Code 1.74 runs Chromium 102: no `linear()`, no View Transitions, no
  `interpolate-size`. The spring is `cubic-bezier(.34,1.4,.52,1)`, section height
  is measured and animated by hand (`useCollapseHeight`), reordering is FLIP
  (`useFlipList`). If the minimum rises to 1.90+ (Chromium 124), this can be
  swapped for `linear()` and View Transitions.
- **Nothing animates per event.** A turn emits hundreds of events per second;
  only mounting, unmounting, reordering and direct interaction animate. The
  streaming cursor is pure CSS.
- **`prefers-reduced-motion` turns everything off**, including the click's
  `transform: scale` and the ripple.
- **A large diff leaves the sidebar**: a compact card in the chat, and the
  editor's native diff to actually read it.
