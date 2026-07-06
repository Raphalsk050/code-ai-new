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
