# Code-AI — VSCode extension

Drives the Code-AI agent from VSCode by spawning the Python **stdio JSON-RPC bridge**
(`code-ai bridge`) and rendering the event stream in a polished chat webview (React), in the
spirit of Claude / Codex / Cline:

- role-distinguished message rows with avatars,
- **markdown** assistant output with syntax-highlighted code blocks (marked + highlight.js,
  sanitized with DOMPurify),
- live streaming with a typing indicator and caret,
- collapsible **thinking** blocks and **tool-call cards** with running/done/failed status,
- a **permission dropdown** in the header (Ask every time / Auto / Bypass) wired to the agent,
- a modal **approval popup** for gated tool calls — allow once / for the session, or **deny with a
  text field** to tell the agent why or what to do instead (the text reaches the model),
- an auto-growing composer (Enter to send, Shift+Enter for newline).

The extension lives in its own **activity-bar container** (left sidebar) with a "CI" icon; the
**“Code-AI: Focus Chat”** command reveals it.

Still **not** implemented (intentional follow-ups): injecting editor context (open file /
selection / workspace), native VSCode diffs for `edit_code`, and the full `ask_user` round-trip.

## How it works

```
Webview (React)  ◄── postMessage ──►  Extension host  ◄── stdio JSON-RPC ──►  code-ai bridge
  reducer.ts (port of view_models.apply)   bridge-client.ts                    (Python core)
```

- Bus events arrive as JSON-RPC `event` notifications and feed `webview/reducer.ts`, a direct
  port of the subset of `src/code_ai/ui/terminal/view_models.py` used here. **Event names are the
  versioned contract** — keep the two reducers aligned.
- User actions (`submit`, `cancel`, `resolveApproval`) become JSON-RPC requests to the bridge,
  mapping onto the `CodeAIApplication` facade.

## Build & run

```bash
cd vscode-extension
npm install
npm run build        # bundles dist/extension.js and dist/webview.js (npm run watch for dev)
```

Then open this folder in VSCode and press **F5** to launch an Extension Development Host. In that
window run the command **“Code-AI: Open”** (Cmd/Ctrl-Shift-P).

## Settings

- `code-ai.command` (default `code-ai`): the executable to launch. The extension appends `bridge`.
  When left at the default, the extension auto-detects `<workspace>/.venv/bin/code-ai` (VSCode
  launched from the GUI does not inherit your shell `PATH`, so the bare `code-ai` usually fails with
  `spawn code-ai ENOENT`). Set this to an absolute path to override, e.g.
  `/path/to/python_agent/.venv/bin/code-ai`.
- `code-ai.args`: extra arguments inserted before `bridge` (e.g. `["--config", "/path/config.json"]`).

The bridge runs with the first workspace folder as its cwd.

## Keyboard

- **Cmd/Ctrl+Enter** in the composer sends the prompt.
