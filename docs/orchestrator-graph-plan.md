# Plan — Orchestrator Graph (Blueprint-style agent orchestration in the VSCode extension)

> **Status:** built on `feature/orchestration-test` (M0–M4). Two deliberate departures from
> what follows, both driven by using it:
> - **No node palette.** Nodes come from the right-click menu, Blueprint-style: a filter box,
>   sections, and — when a wire is dragged off a pin and dropped on empty canvas — only the
>   nodes that pin can actually reach, created already connected. A pin nothing accepts opens
>   an empty menu rather than doing nothing.
> - **Undo is snapshot-based**, not a per-command inverse (section 8 said command stack). A
>   whole-graph snapshot per mutation makes "undo restores exactly what was there" true by
>   construction; the invariant test in `webview/flow/__tests__/store.test.ts` runs it over
>   every action.
>
> **Target:** `vscode-extension/` (new custom editor) + `src/code_ai/core/graph/` (new runtime)
> **How to read this:** section 1 is the four decisions already taken. Sections 3–4 are the
> contract (file format + node catalog) — agree on those before writing code. Sections 5–8 are
> the four layers, each with `file:line` anchors into today's code. Section 10 is the execution
> order.

---

## 0. TL;DR

A node graph, authored in a VSCode editor tab, that orchestrates Code-AI sub-agents the way an
Unreal Blueprint orchestrates gameplay logic: an exec line defines the order, data pins carry
typed values, and pressing Play runs it with circles travelling the wires.

Nothing about the agent runtime needs inventing. `SubagentRuntime.build()`
(`src/code_ai/core/subagents/runtime.py:90`) already produces a **fully isolated agent** from a
profile — its own conversation, event bus, usage ledger, and a tool registry restricted to the
profile's capabilities — and already accepts a per-agent model override. A **Profession node is a
`SubagentProfile`**, and the graph interpreter is a scheduler on top of what
`SubagentCoordinator.dispatch()` (`coordinator.py:112`) already does for one fan-out.

Four layers, in dependency order:

| Layer | Where | What is new |
|---|---|---|
| **Format** | `vscode-extension/src/flow-schema.ts` + `src/code_ai/core/graph/models.py` | `*.flow.json` schema v1, one contract mirrored on both sides |
| **Runtime** | `src/code_ai/core/graph/` + `src/code_ai/app/graph_runner.py` | compile/validate, exec-token interpreter, `graph.*` event vocabulary |
| **Wire** | `src/code_ai/bridge/server.py` | 6 methods: `graphCatalog`, `graphValidate`, `graphRun`, `graphStop`, `graphAnswer`, `graphListModels` |
| **Editor** | `vscode-extension/webview/flow/` | hand-rolled SVG canvas, inspector, undo stack, play indicator |

---

## 1. Decisions taken

| # | Decision | Why |
|---|---|---|
| **D1** | **`CustomTextEditorProvider` bound to `*.flow.json`** | Save/load are not features to build: Ctrl+S, the dirty dot, tab reuse, external-change reload and `git diff` all come from VSCode owning the document. The canvas also gets a full editor tab instead of a 300px sidebar. The toolbar's Save/Load become thin wrappers over `workspace.save` / `window.showOpenDialog`. |
| **D2** | **Execution in the Python backend, driven by new bridge methods** | Reuses `SubagentRuntime`, capability gating (`FORBIDDEN_SUBAGENT_CAPABILITIES`, `profiles.py:16`), the approval gateway, the event bus and the usage ledger. A TypeScript interpreter would have to fake all five, and would lose per-node models and per-node context isolation. |
| **D3** | **Hand-rolled SVG + React, zero new npm dependencies** | Matches how the rest of the webview is built (hand-rolled styles in `webview/styles/base.ts`, a test runner made of esbuild + `node:test`). React Flow would cost an `npm install` on a network that blocks registries, plus ~120KB in the bundle, to save pan/zoom and bezier maths that are ~400 lines here. |
| **D4** | **M1 is a thin vertical slice that actually runs** | Start → Profession → Print → End, with a Model node and the play indicator. The runtime is the risky half; proving it end to end before building loops, variables and undo is what keeps a rewrite from landing in week three. |

**Also decided here** (smaller, but they shape the code):

- **D5 — API keys never enter the file.** `.flow.json` gets committed to git. A Model node stores
  `api_key_ref`, one of `"session"` (use `AppConfig.api_key`), `"env:NAME"`, or `"secret:NAME"`
  (VSCode `SecretStorage`). A literal key in that field is a validation error with a pointer to
  this rule, and the inspector offers no free-text key box.
- **D6 — Undo/redo belongs to the webview, not to the document.** The canvas keeps a command
  stack; VSCode's editor-level undo is not used for graph edits (two undo stacks over one
  document fight each other and lose work).
- **D7 — A graph run occupies the session's turn slot.** The app runs one model turn at a time, so
  `graphRun` refuses with a clear message while a turn is running, and `cancel` stops a graph the
  same way `graphStop` does.

---

## 2. What already exists (anchors)

Read these before writing anything — most of the runtime is a composition of them.

| Piece | Anchor | Relevance |
|---|---|---|
| `SubagentProfile` | `src/code_ai/core/subagents/profiles.py:29` | Pure config: capabilities + budgets + role prompt. **This is a Profession.** |
| `default_profile_registry()` | `profiles.py:179` | Today: explorer, coder, reviewer. Professions extend this list. |
| `SubagentRuntime.build()` | `src/code_ai/core/subagents/runtime.py:90` | Builds an isolated orchestrator; `model=` already overrides the model name. |
| `SubagentRuntime._child_config()` | `runtime.py:162` | `dataclasses.replace` on `AppConfig` — where a per-node `base_url`/`api_key` hooks in. |
| `SubagentCoordinator.dispatch()` | `src/code_ai/core/subagents/coordinator.py:112` | Concurrency cap, circuit breaker, retry, child-event forwarding, cancel mirroring. |
| `SubagentReport` | `src/code_ai/core/subagents/report.py:42` | The whole child→parent channel. A Profession node's `report` output pin. |
| `GoalRunner` | `src/code_ai/app/goal_runner.py:28` | The template for `GraphRunner`: a loop above turns, collaborators injected as callables, zero import of `CodeAIApplication`. |
| `WireApprovalGateway` | `src/code_ai/bridge/approval.py:8` | Parks an `asyncio.Future` keyed by `call_id` until the client answers. **This is the User Chat node.** |
| Bridge handler table | `src/code_ai/bridge/server.py:391` | Where the new methods register. `_CONCURRENT_METHODS` at `:432`, background turns at `:144`. |
| `create_provider()` | `src/code_ai/providers/factory.py:21` | `PROVIDER_BAKED_SETTINGS = {api_key, api_mode, base_url}` — a per-node endpoint needs its own provider instance. |
| `list_available_models()` | `src/code_ai/providers/model_listing.py:46` | Takes an `AppConfig`; the node's "List models" button passes a config override. |
| Webview wiring | `vscode-extension/src/extension.ts:506` | `resolveWebviewView` + the `onDidReceiveMessage` switch at `:518`. |
| HTML/CSP shell | `extension.ts:949` | `renderHtml` — copy it for the second bundle, with its own nonce. |
| esbuild config | `vscode-extension/esbuild.mjs:20` | The webview bundle; a second entry point goes beside it. |
| Webview test runner | `vscode-extension/scripts/test-webview.mjs:23` | Globs `{webview,src}/**/*.test.ts` — new tests are picked up with no config. |
| Webview e2e harness | `vscode-extension/e2e/harness.mjs` | The real bundle in Playwright against a fake host. Needs a flag to load `flow.js`. |

---

## 3. The file format (`*.flow.json`, schema v1)

One contract, two mirrors: `vscode-extension/src/flow-schema.ts` (types + type guard) and
`src/code_ai/core/graph/models.py` (pydantic, `extra="forbid"`, same style as
`src/code_ai/core/goal/models.py`). A mismatch must fail loudly at `graphValidate`, never
silently at run time.

```json
{
  "version": 1,
  "name": "release-pipeline",
  "variables": [
    { "id": "v1", "name": "ticket", "type": "string", "default": "" },
    { "id": "v2", "name": "attempts", "type": "int", "default": 0 }
  ],
  "nodes": [
    { "id": "n1", "kind": "start", "pos": [0, 0], "data": {} },
    { "id": "n2", "kind": "model", "pos": [-260, 150],
      "data": { "api_mode": "completions", "base_url": "http://localhost:11434/v1",
                "api_key_ref": "session", "model": "qwen3.6-35b-a3b",
                "temperature": 0.2, "top_p": null, "top_k": null } },
    { "id": "n3", "kind": "profession", "pos": [280, 0],
      "data": { "profession": "architect", "task": "Design the import pipeline.",
                "role_override": "", "max_steps": 0, "timeout_s": 0 } },
    { "id": "n4", "kind": "print", "pos": [640, 0], "data": { "label": "design" } },
    { "id": "n5", "kind": "end", "pos": [860, 0], "data": {} }
  ],
  "edges": [
    { "id": "e1", "kind": "exec", "from": ["n1", "out"],    "to": ["n3", "in"] },
    { "id": "e2", "kind": "data", "from": ["n2", "model"],  "to": ["n3", "model"] },
    { "id": "e3", "kind": "exec", "from": ["n3", "out"],    "to": ["n4", "in"] },
    { "id": "e4", "kind": "data", "from": ["n3", "report"], "to": ["n4", "value"] },
    { "id": "e5", "kind": "exec", "from": ["n4", "out"],    "to": ["n5", "in"] }
  ],
  "view": { "pan": [0, 0], "zoom": 1 }
}
```

Rules the writer must honour so the file stays diff-friendly:

1. Keys serialized in a **fixed order**, 2-space indent, trailing newline. A node moved by four
   pixels must not reorder the file.
2. `pos` rounded to integers; `view` is the only cosmetic state persisted.
3. Ids are short and stable (`n7`, `e12`, `v3`) — never regenerated on save.
4. Unknown `kind` or unknown `data` key is a validation error, not a crash. Forward
   compatibility is a reader problem, and the reader is the one who must say so.

---

## 4. Node catalog

**Two pin kinds.** Exec pins (white, square) carry the flow; exactly one token is live at a time,
which is what makes the play indicator unambiguous. Data pins (round, coloured by type) are
**pull-based**: a node about to execute pulls its data inputs, evaluating pure nodes on demand.

**Pin colours** (Blueprint-adjacent, tuned for VSCode themes):

| Type | Colour | Hex |
|---|---|---|
| exec | white | `#FFFFFF` |
| bool | dark red | `#8C1B1B` |
| int | teal | `#1F9E8C` |
| float | lime | `#A4E22E` |
| string | magenta | `#E040FB` |
| model | link blue | `var(--accent)` |
| any / report | grey | `#A0A0A0` |

### Nodes

| Kind | Pins | Fields | Backend behaviour |
|---|---|---|---|
| `start` | exec out | — | Root of the main line. Exactly one per graph. |
| `model` | data out `model` | `api_mode`, `base_url`, `api_key_ref`, `model` (+ **List models** button), `temperature`, `top_p`, `top_k` | Pure node. Resolves to a provider from `create_provider()` on a `dataclasses.replace`d config, cached per `(api_mode, base_url, key)` so ten nodes on one endpoint share one client. |
| `profession` | exec in/out; data in `model?`, `task`; data out `report` | profession dropdown, `role_override`, `max_steps`, `timeout_s` (0 = profile default) | `SubagentRuntime.build(profile, model=…, provider=…)` then one run; the output pin carries `SubagentReport.summary`. |
| `sequence` | exec in; exec out `0..n` ("+ Add pin") | — | Fires each output in order, awaiting the whole downstream chain of one before starting the next. |
| `decision` | exec in; exec out `true`/`false`; data in `condition` (bool) *or* `prompt` (string) + `model?` | `mode`: `bool` \| `judge` | `bool` reads the pin. `judge` makes one bounded model call answering yes/no, the same shape as `CriterionKind.JUDGE` (`src/code_ai/core/goal/models.py:27`). |
| `loop` | exec in; exec out `body`, `completed`; data out `index`, `item?` | `kind`: `count` \| `while` \| `foreach`; `count` / `max_iterations` | A hard iteration cap always applies, mirroring `GoalConfig.max_iterations`. `while` re-pulls its condition each lap. |
| `user_chat` | exec in/out; data in `prompt?`; data out `answer` | `title` | Parks a future keyed by `call_id`, exactly as `WireApprovalGateway.request_approval` does, and emits `graph.user_chat.requested`. Resumes on `graphAnswer`. No timeout: a graph waiting on a human is paused, not failing. |
| `var_set` | exec in/out; data in `value` | variable picker | Writes the run scope; emits `graph.variable.changed`. |
| `var_get` | data out `value` | variable picker | Pure node. |
| `print` | exec in/out; data in `value` | `label` | Emits `graph.print`; shown in the editor's log panel and mirrored to a "Code-AI Flow" output channel. |
| `end` | exec in; data in `result?` | — | Terminates the run with `status: completed`. |

### Professions → profiles

Each profession is a `SubagentProfile` in a new `src/code_ai/core/graph/professions.py`, composed
with `default_profile_registry()` so a graph can also target the three existing agents. The
`__post_init__` check at `profiles.py:46` already refuses forbidden capabilities, so a bad
profession fails at import rather than mid-run.

| Profession | Capabilities | Writes | Notes |
|---|---|---|---|
| `architect` | `LOCAL_READ`, `WEB` | no | Designs and reports; never edits. |
| `code_writer` | `LOCAL_READ`, `LOCAL_WRITE`, `PROCESS`, `WEB` | yes | The existing `coder` profile, renamed in the UI only. |
| `documenter` | `LOCAL_READ`, `LOCAL_WRITE`, `WEB` | yes | Docstrings and repo docs. No `PROCESS`: it has nothing to run. |
| `wiki_writer` | `LOCAL_READ`, `LOCAL_WRITE`, `WEB` | yes | Long-form prose; a higher step budget, and low temperature is a Model-node choice. |
| `ui_designer` | `LOCAL_READ`, `LOCAL_WRITE`, `WEB` | yes | Markup and styles; pairs with the existing design tools. |
| `teacher` | `LOCAL_READ`, `WEB` | no | Explains to the user; output is prose, not edits. |
| `reviewer` / `explorer` | existing | no | Reused unchanged from `profiles.py:122` / `:70`. |

The catalog is served over the wire (`graphCatalog`) rather than hardcoded in the webview: adding
a profession must not require shipping a new VSIX.

---

## 5. Runtime (Python)

```
src/code_ai/core/graph/
  models.py        GraphProgram, NodeSpec, EdgeSpec, VariableSpec (pydantic, extra="forbid")
  compile.py       JSON -> GraphProgram + [ValidationIssue]; data-cycle detection
  professions.py   one SubagentProfile per profession, composed with the default registry
  runtime.py       the interpreter: exec tokens, data pull, variable scope, budgets
  events.py        the graph.* emit helpers (one place, so names cannot drift)
src/code_ai/app/
  graph_runner.py  GraphRunner: owns one run, collaborators injected, no app import
```

### Interpreter

- One `asyncio` task per run. An **exec token** starts at `start` and walks exec edges;
  `sequence` / `loop` / `decision` push and pop tokens on an explicit stack — no recursion, so a
  200-node graph cannot blow the Python stack.
- **Data pull, memoised per node execution**: pulling the same Model node from six Profession
  nodes builds one provider and one value.
- **Budgets** in a new `GraphConfig` beside `GoalConfig` (`src/code_ai/config/models.py:484`):
  `max_nodes_executed` (200), `max_seconds` (3600), `max_loop_iterations` (50),
  `max_concurrent_nodes` (1 for M1). Exceeding one ends the run `EXHAUSTED`, naming the node that
  tripped it.
- **Cancellation** reuses the coordinator's mirroring (`coordinator.py:427`): `graphStop` sets the
  cancel event, the in-flight sub-agent unwinds, the run ends `stopped`.
- **Approvals** are untouched: a `code_writer` node's first write raises the usual
  `tool.approval.requested`, and the chat panel's approval card answers it. A graph's permission
  mode defaults to the session's.

### Event vocabulary (all `event_version: 1`)

The editor is a pure function of this stream — no polling, no separate run-state request.

| Event | Payload | Drives |
|---|---|---|
| `graph.run.started` | `run_id`, `name`, `node_count` | Play button → running state |
| `graph.node.started` | `run_id`, `node_id`, `kind`, `label` | Node glow |
| `graph.node.completed` | `run_id`, `node_id`, `status`, `summary`, `seconds`, `usage` | Node badge + tooltip |
| `graph.node.failed` | `run_id`, `node_id`, `error` | Red node + log entry |
| `graph.edge.active` | `run_id`, `edge_id` | **The travelling circles** |
| `graph.edge.traveled` | `run_id`, `edge_id` | Persistent "already walked" wire tint |
| `graph.print` | `run_id`, `node_id`, `text` | Log panel |
| `graph.variable.changed` | `run_id`, `name`, `value_preview` | Live values in the inspector |
| `graph.loop.iteration` | `run_id`, `node_id`, `index`, `total` | Loop node counter |
| `graph.user_chat.requested` | `run_id`, `node_id`, `call_id`, `prompt` | Inline chat box on the node |
| `graph.run.completed` | `run_id`, `status`, `seconds`, `usage` | Play → idle |

Sub-agent child events already surface as `subagent.progress` (`coordinator.py:436`); add the
owning `node_id` to that payload when the dispatch came from a graph, so per-node progress is
attributable without a second channel.

---

## 6. Bridge methods

Registered in `_HANDLERS` (`src/code_ai/bridge/server.py:391`), mirrored in
`vscode-extension/src/protocol.ts` (`BridgeMethod`).

| Method | Params | Result | Concurrent? |
|---|---|---|---|
| `graphCatalog` | — | professions (name, description, writes, capabilities), variable types, node kinds | no |
| `graphValidate` | `graph` | `{ ok, issues: [{node_id, pin, message}] }` | no |
| `graphRun` | `graph`, `variables?` | `{ ok, run_id }`; events carry the rest | **yes** — background task, like `_start_background_turn` (`server.py:144`) |
| `graphStop` | `run_id` | `{ ok }` | **yes** |
| `graphAnswer` | `run_id`, `call_id`, `text` | `{ ok }` | no — resolves a future, must not queue behind the run |
| `graphListModels` | `api_mode`, `base_url`, `api_key_ref` | `{ models: [...] }` | **yes** — network call |

`graphRun` refuses while a turn is running (D7), with a message the editor shows verbatim.

---

## 7. Extension host

### `package.json`

```json
"activationEvents": ["onView:code-ai.chat", "onCustomEditor:code-ai.flow"],
"contributes": {
  "customEditors": [{
    "viewType": "code-ai.flow",
    "displayName": "Code-AI Orchestrator",
    "selector": [{ "filenamePattern": "*.flow.json" }],
    "priority": "default"
  }],
  "commands": [
    { "command": "code-ai.newFlow",  "title": "Code-AI: New Orchestrator Graph" },
    { "command": "code-ai.runFlow",  "title": "Code-AI: Run Graph" },
    { "command": "code-ai.stopFlow", "title": "Code-AI: Stop Graph" }
  ]
}
```

### `src/flow-editor.ts` — `FlowEditorProvider implements vscode.CustomTextEditorProvider`

- `resolveCustomTextEditor`: its own HTML shell (copy `renderHtml`, `extension.ts:949`) pointing at
  `dist/flow.js`, its own nonce, `localResourceRoots: [dist]`.
- Document → webview: push the parsed graph on open and on `onDidChangeTextDocument`.
- Webview → document: `{ type: "edit", graph }` becomes a `WorkspaceEdit` replacing the full text.
  VSCode then owns dirty state and Ctrl+S.
- **Echo guard:** remember the last text written and ignore the change event it produces, or the
  editor rewrites itself on every keystroke.

### Bridge sharing — the one refactor M0 must do

Today `ChatViewProvider` owns the bridge: it spawns in `resolveWebviewView` (`extension.ts:506`)
and disposes in `onDidDispose`. A flow editor tab needs the *same* Python process, and must work
when the chat view was never opened.

Extract a **`BridgeSession`** (`src/bridge-session.ts`): spawn/restart/dispose, one event
subscription fanned out to N registered webviews, `request()` / `send()` passthrough. Owned by
`activate()`, injected into both providers. A mechanical move of ~120 lines that also fixes the
current coupling where restarting the bridge requires the chat view to exist.

### Build

`esbuild.mjs`: a third config, same shape as `webviewConfig` (`esbuild.mjs:20`), entry
`webview/flow/index.tsx` → `dist/flow.js`. `scripts/verify-vsix.mjs` asserts the new bundle ships.

---

## 8. Editor (webview)

```
webview/flow/
  index.tsx        mount, host plumbing, event -> store
  store.ts         reducer over {nodes, edges, variables, selection, viewport, run}
  commands.ts      every mutation as {label, do, undo} -> the undo stack
  canvas.tsx       <svg>: grid, pan/zoom, marquee select, drag
  node-view.tsx    node chrome, pins, inline fields
  wire.tsx         bezier + travelling dot
  inspector.tsx    selection details; variables with the + button
  toolbar.tsx      Play/Stop centred top, node palette, Save
  log.tsx          print output, run status, validation issues
  clipboard.ts     copy/paste of a selected subgraph
  hit-test.ts      pin snapping and edge legality by pin type
```

Shared with the host through `src/flow-schema.ts` — the webview imports the same types the host
validates against, exactly as it imports `src/protocol.ts` today (`webview/index.tsx:4`).

**Undo/redo (D6).** Command stack, cap 100, drags coalesced into one entry on mouse-up. Every
committed command posts the new graph to the host so the document goes dirty — including undo and
redo. The keyboard is handled inside the webview; VSCode routes Ctrl+Z to the focused webview.

**Copy/paste.** Serialize selected nodes plus the edges internal to the selection as
`{ "codeai.flow.clip": 1, nodes, edges }`. Paste reads the DOM `paste` event's `clipboardData` —
the dependable path in a webview, since `navigator.clipboard.readText()` needs focus plus a gesture
and fails silently otherwise — falls back to an in-memory buffer, remaps ids and offsets by +24,+24.

**Play indicator.** On `graph.edge.active`, attach a `<circle>` with `<animateMotion>` to that
wire's path, `dur` proportional to path length, `repeatCount="indefinite"`; remove it on the next
`graph.node.started`. `graph.edge.traveled` leaves a tinted wire so the walked path stays readable.
Cap concurrently animated edges at 20.

**Validation feedback.** `graphValidate` runs debounced (400ms) after every edit; issues mark the
offending node red and list in the log panel. Play is disabled while `ok === false`.

**Strings are pt-BR**, matching the existing panels (`webview/panels/agents-panel.tsx:12`); code
comments stay in English.

---

## 9. Threats to the design (read before coding)

| Risk | Mitigation |
|---|---|
| **An API key committed to git** | D5: `api_key_ref` only. The validator rejects anything shaped like a literal key, and the Model node has no free-text key field. |
| **Runaway graph** (a loop of writers) | Hard caps in `GraphConfig`; `graphStop` cancels in flight; approvals still fire per write. |
| **Turn-slot contention** | D7: refuse with a clear message; the chat composer shows that a graph is running. |
| **Two undo stacks over one document** | D6: the webview owns undo; document edits are a consequence, never a source. |
| **Schema drift between TS and Python** | `graphValidate` is the only source of truth for legality; the TS type guard exists for editor ergonomics, not as a second validator. |
| **Frozen-binary build** | No new pip dependency (pydantic is already in use, `core/goal/models.py:6`) and no new npm dependency (D3). Nothing in the graph runtime may import a third-party module at module level. |
| **Canvas at scale** | Plain SVG is fine to ~200 nodes; virtualization is explicitly out of scope, and the node cap says so. |

---

## 10. Milestones

Estimates assume one developer already familiar with this codebase.

### M0 — Plumbing (≈1 day)

1. Extract `BridgeSession` from `ChatViewProvider`; both providers take it injected.
2. Third esbuild config → `dist/flow.js`; `verify-vsix.mjs` asserts it ships.
3. `FlowEditorProvider` renders a placeholder that round-trips `*.flow.json` text.

**Done when:** opening a `.flow.json` shows the custom editor, an edit from the webview marks the
tab dirty, and Ctrl+S writes the file.

### M1 — Vertical slice that runs (≈4–6 days)

1. Schema v1 in both mirrors; `compile.py` plus validation issues.
2. `start`, `model`, `profession`, `print`, `end`; exec and data pins; drag-to-connect with type
   checking.
3. `GraphRunner` plus the interpreter for a linear chain; `graphCatalog`, `graphValidate`,
   `graphRun`, `graphStop`.
4. `graph.*` events; node states, log panel, travelling circles.
5. Per-node provider (`provider=` on `SubagentRuntime.build`, cached by endpoint) and the **List
   models** button via `graphListModels`.

**Done when:** a three-node graph runs an `architect` on a local Ollama model, its report prints in
the log, and the circles walk Start → Profession → Print → End.

### M2 — Control flow and data (≈3–4 days)

`sequence`, `decision` (both modes), `loop` (three kinds, capped), variables in the inspector with
the + button and type colours, `var_get` / `var_set`, live values during a run.

**Done when:** a loop of `code_writer` → `reviewer` exits on a judge decision, with the iteration
count visible on the loop node.

### M3 — Editor ergonomics (≈3–4 days)

Copy/paste, undo/redo, marquee select, pan/zoom polish, node search palette, alignment snapping,
validation markers, and the empty-graph scaffold behind `code-ai.newFlow`.

**Done when:** the undo test (`do → undo` restores the exact prior state, for every command) passes
for all node kinds, and a copied subgraph pastes with remapped ids.

### M4 — Human in the loop (≈2–3 days)

The `user_chat` node with pause/resume, per-node usage and cost, run history in the log panel, and
`node_id` attribution on `subagent.progress`.

**Done when:** a graph stops at a `user_chat` node, the user types an answer on the node, and the run
continues with that text on the `answer` pin.

---

## 11. Tests

Mirrors the conventions already in place — no new tooling.

**Python** (`tests/unit/`, in the style of `test_goal_runner.py`):

- `test_graph_compile.py` — valid graph; unknown kind; missing `start`; two `start`s; data cycle;
  pin type mismatch; literal API key rejected; unknown `data` key.
- `test_graph_runtime.py` — linear chain with a fake dispatcher; sequence order; branch both ways;
  loop hits its cap; `var_set` / `var_get`; print payload; stop mid-node; node budget exhaustion.
- `test_graph_professions.py` — every profession builds; none requests a forbidden capability;
  read-only professions really are read-only.
- `test_graph_events.py` — exact event order and payload keys for a four-node run.
- `tests/integration/test_graph_bridge.py` — `graphRun` over the real bridge with a stub provider;
  `graphStop` unwinds; `graphAnswer` resumes a parked `user_chat`.

**Webview** (globbed automatically by `scripts/test-webview.mjs`):

- `webview/flow/__tests__/store.test.ts`, `commands.test.ts` (the do/undo invariant),
  `hit-test.test.ts` (pin legality), `clipboard.test.ts` (id remap), `schema.test.ts` (round-trip
  byte stability: parse → serialize → identical text).

**e2e** (`e2e/flow.e2e.mjs`, registered in `SPECS`, `e2e/run.mjs:12`): the harness gains a flag to
load `flow.js`. Specs: add a node from the palette; connect two pins; reject an illegal connection;
Play disabled while invalid; a fake `graph.edge.active` renders a travelling dot.

---

## 12. Open questions

1. **Where do graphs live by default?** Proposal: `.code-ai/flows/` in the workspace, so
   `code-ai.newFlow` has an obvious home and graphs are shareable through git.
2. **Should a graph be invokable as a slash command?** The asset catalog
   (`src/code_ai/app/slash_catalog.py`) already lists workflows and skills; `/flow release` running
   a saved graph headlessly is a small addition once M1 lands, and it is how this feature becomes
   useful outside the editor.
3. **Concurrency.** `max_concurrent_nodes` is 1 for M1. Parallel branches are the natural next step
   (the coordinator already caps and semaphores fan-out), but they make the single travelling token
   ambiguous — the indicator needs a second design pass first.
