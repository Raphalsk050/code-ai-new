# Code-AI

Code-AI is a terminal-based coding agent with a small application facade, typed JSON-serializable events, normalized model providers, strict workspace-bound tools, headless execution, and an optional Textual UI.

## Architecture

- `cli`: argument parsing, process exit codes, headless/TUI selection.
- `ui`: rendering and user interaction only.
- `app`: public facade used by CLI, UI, and embedding clients.
- `core`: provider-independent orchestration and state.
- `providers`: OpenAI Responses, Chat Completions, and native Ollama adapters.
- `context`: conversation state, token accounting, and compression.
- `events`: event contracts, event bus, and JSON Lines sinks.
- `tools`: schemas and implementations for files, commands, terminals, review, system info, and web search.
- `interop`: adapters that map other agents' on-disk conventions (rules, skills, workflows) onto Code-AI sources.
- `config`: defaults, loading, validation, and redacted display.

The CLI and UI do not parse provider SDK objects or execute tools directly. Provider adapters convert SDK-specific responses into normalized internal models at the boundary.

## Install

```bash
python -m pip install -e ".[dev]"
```

## Configuration

The default configuration path is:

```text
~/.code-ai/config.json
```

Create a safe example:

```bash
code-ai config init
```

Display a redacted configuration:

```bash
code-ai config show
```

Safe example:

```json
{
  "api_key": "",
  "api_mode": "ollama",
  "base_url": "http://localhost:11434/v1",
  "permission_mode": "ask",
  "language": "en",
  "model": "gemma4:31b-cloud",
  "show_ui": true,
  "ssl_verification": false,
  "use_remote_conversation_state": true,
  "workspace": "/absolute/path/to/workspace"
}
```

`OPENAI_API_KEY` and `BASE_URL` override file values. API keys are redacted in config output, logs, events, tests, and UI-facing payloads.

## API Modes

- `responses`: OpenAI Responses or compatible `/v1/responses`.
- `completions`: modern message-oriented Chat Completions (`/v1/chat/completions`).
- `chat_completions`: alias for `completions`.
- `ollama`: native Ollama `/api/chat`.

`completions` intentionally means Chat Completions, not the legacy text-only completions endpoint, because the legacy endpoint is not a reliable base for tool calling.

## Tool permissions

`permission_mode` controls whether the agent must ask before running tools that
mutate the workspace or execute commands. When a tool would be blocked by the
planner policy, the user is asked to approve it instead of the call failing
outright.

- `ask` (default): in the terminal UI, an approve/deny modal pops up before any
  tool that writes files, runs commands, or drives a terminal — and whenever the
  policy blocks a tool. The modal offers **Deny**, **Allow once**, and **Always
  allow** (remembers the tool, or for `execute_command` the program name, for the
  rest of the session).
- `auto`: policy-allowed tools run without prompting; only a policy denial
  escalates to an approval prompt.
- `bypass`: every tool runs immediately, no prompts ("yolo" mode).

Change it at runtime in the TUI with `/mode ask|auto|bypass` (persisted to
`config.json`). The active mode shows in the status line (`perm <mode>`).

Non-interactive runs (headless, embedding clients) have no one to prompt, so
gated tools are denied unless the mode is `auto` (for policy-allowed tools) or
`bypass`. Set `permission_mode` accordingly for automation.

## Sub-agents

The agent can delegate focused subtasks to isolated sub-agents through the `dispatch_agent` tool, the way a lead engineer hands work to specialists.
Each sub-agent runs its own provider/tool loop with a fresh conversation, its own usage ledger, and a tool registry restricted to its role - no mutable state is shared, and a sub-agent cannot delegate further.
Communication is by message only: the parent passes a standalone prompt and the sub-agent returns a self-contained report.

Three profiles ship by default:

- `explorer`: read-only investigation (search and read the workspace and web). Used to fan out research in parallel.
- `coder`: one focused, self-contained change (read, edit, run, verify).
- `reviewer`: independent code review (reads and runs the review/build/test tools, never edits source).

A single `dispatch_agent` call may carry several tasks; they run concurrently up to `max_concurrent_subagents`.
Delegation is resilient by construction: each sub-agent has a wall-clock timeout scoped to its profile, transient failures retry with backoff, and a per-profile circuit breaker stops dispatching to a role that keeps failing.
Every delegation resolves to a structured report - unknown types, limits, timeouts, and crashes degrade into feedback the model reacts to rather than crashing the turn.

Because `dispatch_agent` carries the `delegate` capability, `ask` mode prompts once at the delegation boundary; the sub-agents then run without further prompts (so a parallel fan-out never blocks on approval).
Relevant limits live under `budgets`: `max_subagent_depth` (default 1, no recursion), `max_concurrent_subagents`, `max_subagents_per_turn`, `subagent_explorer_timeout_s`, `subagent_worker_timeout_s`, and the `subagent_retry_max_attempts` / `subagent_circuit_*` resilience knobs.

## Rules, skills, and workflows

Three kinds of reusable instruction live on disk, and they differ only in when they apply.

- **Rules** are mandatory and always injected into the system prompt.
Global rules live in `~/.code-ai/rules`, project rules in `<workspace>/.code-ai/rules` so they can be committed with the repository.
Author one with the `create_rule` tool.
- **Skills** load on demand when the task matches.
They live in `~/.code-ai/skills`, either as `<name>/SKILL.md` or as a flat `<name>.md`.
The agent sees a catalog of names and descriptions every session and loads the fitting one with `use_skill`; `create_skill` writes new ones.
- **Workflows** are named procedures the user runs on demand.
Global workflows live in `~/.code-ai/workflows`, project workflows in `<workspace>/.code-ai/workflows`.
Each one is a markdown file whose body is the procedure.

In the terminal UI every workflow and every skill is also a slash command, with `/` autocomplete and Tab to accept.
`/deploy` runs `deploy.md`, `/pdf-magic` forces that skill for the next turn instead of waiting for the model to match it, and anything typed after the name travels with it (`/release 1.4.0`, `/pdf-magic extract the table from report.pdf`).
`/workflows` and `/skills` list what is available, with the origin of each.
Resolution order is built-in commands, then workflows, then skills - so neither can shadow `/status`, and a name that exists as both runs the workflow.
The agent reaches the same files through `use_workflow` and `use_skill` when a request names one in prose or a task simply matches a skill's description.

### Assets written for other agents

Rules, skills, and workflows authored for [Cline](https://cline.bot) are picked up as they are, with no migration, copying, or configuration.
Open a workspace that already has them and they are simply in effect.

Cline reads two workspace layouts - the current `.cline/` one and the older `.clinerules` one - and so does Code-AI:

| Kind | Cline location |
| --- | --- |
| Global rules | `~/.cline/rules/`, `~/Documents/Cline/Rules/` (legacy) |
| Global workflows | `~/.cline/workflows/`, `~/Documents/Cline/Workflows/` (legacy) |
| Global skills | `~/.cline/skills/` |
| Project rules | `<workspace>/.cline/rules/`, `<workspace>/.clinerules` (a single file, or a directory of rule files) |
| Project workflows | `<workspace>/.cline/workflows/`, `<workspace>/.clinerules/workflows/` |
| Project skills | `<workspace>/.cline/skills/`, `<workspace>/.clinerules/skills/`, `<workspace>/.agents/skills/` |

The install-wide scope is `~/.cline/` in the current layout; the documents folder is the older one and is still read, so both generations of a setup work.

Discovery is additive and every location is optional: absent or unreadable directories are skipped, so an install with none of them behaves exactly as before.
Code-AI's own directories take precedence, so a same-named skill or workflow of yours shadows the imported one; a rule from either place is always applied, and the prompt labels which agent it came from.
`.clinerules/workflows` and `.clinerules/skills` are read as workflows and skills, never as always-on rules.
A skill switched off with `disabled: true` in its `SKILL.md` frontmatter is left out of the catalog and refuses to load, here as in Cline.
Cline's rule and workflow toggles, by contrast, live in the extension's own state and cannot be read from disk, so a rule file present in the folder is treated as active.

Set `CODE_AI_CLINE_HOME` if Cline's legacy documents folder lives somewhere other than `~/Documents/Cline` (on some Linux and WSL setups it is `~/Cline`, which is detected automatically).
`CODE_AI_RULES_DIR`, `CODE_AI_SKILLS_DIR`, and `CODE_AI_WORKFLOWS_DIR` relocate Code-AI's own global directories.

## Sampling and reasoning

The optional `sampling` section tunes how the model generates and whether its
reasoning ("thinking") is captured. Any field left as `null` is omitted from the
request so the endpoint default applies.

```json
{
  "sampling": {
    "temperature": 0.6,
    "top_p": 0.95,
    "presence_penalty": 0.0,
    "frequency_penalty": null,
    "top_k": 20,
    "min_p": 0.0,
    "reasoning_effort": null,
    "reasoning_summary": null,
    "extra_body": {}
  }
}
```

- `temperature`, `top_p`, `presence_penalty`, `frequency_penalty` map to the
  standard OpenAI sampling controls.
- `top_k` and `min_p` are not part of the OpenAI schema, so they are forwarded
  through `extra_body` for OpenAI-compatible servers (vLLM, SGLang, ...). In
  `ollama` mode they go into the request `options`.
- `extra_body` is a free-form passthrough merged into the request body for any
  other vendor-specific knobs.
- If an endpoint rejects a sampling parameter, the provider warns and retries
  the request once without sampling kwargs instead of failing.

Reasoning/"thinking" is captured into `ModelResponse.reasoning` and streamed as
`reasoning_delta` events, by mode:

- `completions`: OpenAI-compatible reasoning servers expose chain-of-thought in
  a non-standard `reasoning_content` field (start the server with a reasoning
  parser, e.g. vLLM `--reasoning-parser deepseek_r1`). Official OpenAI Chat
  Completions does not expose reasoning.
- `responses`: official OpenAI reasoning models never return raw reasoning
  tokens; set `reasoning_effort` (`none`/`minimal`/`low`/`medium`/`high`/`xhigh`)
  and `reasoning_summary` (`auto`/`concise`/`detailed`) to receive a reasoning
  *summary* (streamed via `response.reasoning_summary_text.delta`).
- `ollama`: reasoning arrives in the `thinking`/`reasoning` field.

## Examples

OpenAI:

```json
{
  "api_mode": "responses",
  "base_url": "https://api.openai.com/v1",
  "api_key": "",
  "model": "gpt-4.1"
}
```

OpenAI-compatible endpoint:

```json
{
  "api_mode": "completions",
  "base_url": "http://localhost:1234/v1",
  "api_key": "",
  "model": "local-coder"
}
```

Ollama through OpenAI-compatible Chat Completions:

```json
{
  "api_mode": "completions",
  "base_url": "http://localhost:11434/v1",
  "api_key": "",
  "model": "qwen3-coder"
}
```

Native Ollama:

```json
{
  "api_mode": "ollama",
  "base_url": "http://localhost:11434/v1",
  "api_key": "",
  "model": "qwen3-coder"
}
```

When an OpenAI-compatible local server requires a non-empty API key, Code-AI uses an internal placeholder only during provider construction and never persists it as the user's key.

## Usage

Launch the TUI:

```bash
code-ai
```

Run headless:

```bash
code-ai --headless run "Inspect and fix the failing tests"
```

Stream JSON Lines events:

```bash
code-ai --headless --events-jsonl run "Build the project"
```

### Watching the code being written

A tool call's arguments arrive one fragment at a time, so the file a model is writing exists as a partial, unparseable JSON string long before it exists on disk.
Code-AI decodes that string as it grows and plays the write out as it happens.

A window opens the moment the call starts, before any source exists - a titled frame naming the call and its target, worded like the approval dialog it hands over to:

```text
╭─ write_file ──────────────────────────────────────────────╮
│ ✓ Create / overwrite:  src/cache.py   ·   22 lines        │
│ Why: bound the cache so a long session cannot grow it      │
│                                                            │
│   16 │   def put(self, key: str, value: str) -> None:      │
│   17 │   │   if len(self._items) >= self.limit:            │
│   18 │   │   │   self._items.pop(next(iter(self._items)))  │
╰───────────────────────────────────── src/cache.py ─────────╯
```

The order is deliberate: the frame and the model's reason first, the source filling in underneath, then the approval dialog with the complete code and its diff.
The `reason` argument is declared ahead of the file contents on the writing tools for exactly this - so the *why* streams in before the *what*.
The reason follows `/config learn`, the same switch that shows it in the approval dialog.

The window is a tail, like the TERMINAL panel: it shows the newest rows rather than the whole file.
The complete code still goes through the approval dialog before anything is written (in `ask` mode), and the file itself is on disk afterwards.

It covers every tool that writes to the workspace - `write_file`, `edit_code` (showing the replacement going in, labelled as such, since the diff needs both halves), `create_rule`, `create_skill`.
Tools that merely pass code around, such as `code_review`, are deliberately left out: nothing of theirs is being written.
Writes made by delegated sub-agents are not shown either - several agents write concurrently, and interleaving their files in one window would show a file that never existed.
The AGENTS panel reports what each of them is doing instead.

Colouring a file that is still arriving is the awkward part, and the two obvious approaches are both wrong.
Re-lexing the whole buffer on every fragment is quadratic and stalls the terminal; lexing only the visible rows is cheap but paints them wrong, because a lexer handed the middle of a docstring has no way to know that is where it is - it reads the prose as code and the closing quotes as an *opening* one, and everything after comes out the colour of a string.
So the visible rows are lexed together with a bounded stretch of the source above them, which rebuilds the state the lexer needs without ever growing with the file.
The language settles on the first fragment, from the path or from a known format for tools that write without one, so the code is coloured from its first line rather than after it lands.

Both costs stay flat as the file grows:

- the paint is rate-limited by its own timer rather than by the event rate, capping repaints at ~16/s no matter how fast the model streams;
- each repaint lexes a window, never the file, measuring at ~2 ms whether the file is 200 lines or 6000.

Turn it off with `/config live-code off` (or `"terminal_live_code": false`) on a terminal where any repainting is costly - a slow SSH link, a heavy multiplexer.
Writes then report progress on one line, as before.

A call can also be cut off before it ever arrives: the model stream times out, the endpoint drops it, or the provider discards arguments it could not parse.
Nothing was written in that case, so the window closes rather than sitting on a file nobody is writing, and the runtime asks the model to make the call again.
Without that, the prose the model streamed just before the call - normally the announcement of the change it was about to make - was all that survived the step, and it became the turn's answer: the agent settled into `waiting_user` having said it would implement something and implemented nothing.
Bounded at two attempts, so a stream that keeps breaking still ends the turn with a best-effort reply instead of re-prompting forever.

The stream is also on the event bus, as `call_started` / `writes` / `reason` / `code_offset` / `code_delta` / `code_complete` on `tool.call.progress`, plus `tool.call.interrupted` when a call is cut off, so the VS Code bridge and any other consumer can render the same flow.

### Pasting images

Ctrl+V in the prompt reads the OS clipboard directly, so an image (a screenshot, a copied picture) becomes an `[Image #N]` attachment that travels with the prompt to vision-capable models.
Text on the clipboard is pasted as usual.

Reading the clipboard requires a platform tool:

- macOS: works out of the box via AppleScript; installing `pngpaste` (`brew install pngpaste`) makes image capture faster.
- Windows: works out of the box via PowerShell.
- Linux (Wayland): install `wl-clipboard` (provides `wl-copy`/`wl-paste`).
- Linux (X11): install `xclip` (or `xsel`, text only - it cannot read images).

On Linux the session's display server owns the clipboard, so the matching tool must be installed; when none is found, Ctrl+V shows a notification with the package to install.
PNG, JPEG, WebP and GIF clipboard images are supported.

If the main model is not multimodal, set a vision sidekick with `/config vision-model <name>` (or `"vision_model"` in the config file), e.g. a local `qwen2.5-vl` served by the same provider.
When set, pasted images are transcribed by that model in a one-off call and the description is injected into the conversation as text, so the main model never receives image payloads it cannot read.
If the vision call fails, the raw images are attached as before.
`/config vision-model off` disables it.

## Available Tools

- `read_file`
- `write_file`
- `edit_code`
- `execute_command`
- `control_terminal`
- `read_screen`
- `system_information`
- `web_search`
- `architecture_review`
- `code_review`
- `build_review`
- `use_skill`
- `create_skill`
- `use_workflow`
- `create_rule`
- `dispatch_agent`

File and process tools resolve symlinks and enforce that all operations remain inside the configured workspace.

## Token Accounting And Compression

Provider-reported token usage is exact when present. Pre-request context size may be estimated; estimated values are marked with `~`. Active context size and cumulative usage are separate values, so context compression is based on the active request size, not historical cumulative usage.

Automatic compression runs before the configured context limit is exhausted. Compression keeps the current request, recent turns, and complete tool call/result pairs. Local compression resets remote response-state assumptions for the next provider request.

## Security Boundaries

Code-AI does not expose complete environment dictionaries, API keys, tokens, SSH material, unrelated user files, or raw provider headers in events or logs. Command tools inherit a sanitized environment and reject workspace escapes.

## Known Limitations

- Native persistent terminal control is initially POSIX-oriented and fails clearly on unsupported platforms.
- Ollama and OpenAI-compatible capability support depends on the server and selected model.
- Remote conversation state is used only when supported and falls back to local conversation state if rejected.
- The Textual UI is intentionally compact and consumes events through view models; richer command completion can be extended without touching core orchestration.

## Development

```bash
python -m pytest
python -m ruff check .
python -m compileall src tests
```
