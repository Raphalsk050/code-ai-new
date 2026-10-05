# NOTES

Decisions, trade-offs and loose ends. One entry per block of work, newest at
the top.

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
