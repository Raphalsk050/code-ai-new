# NOTES

Decisions, trade-offs and loose ends. One entry per block of work, newest at
the top.

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
