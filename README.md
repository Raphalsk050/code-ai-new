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
