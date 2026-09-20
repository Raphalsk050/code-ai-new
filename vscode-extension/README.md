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
  Left at the default, it resolves in this order: the binary bundled in the extension
  (`<extension>/bin/code-ai[.exe]`), then a development virtualenv — `.venv/Scripts/code-ai.exe` on
  Windows, `.venv/bin/code-ai` elsewhere — next to the extension and in each workspace folder, then
  the bare name on `PATH` (which usually fails: VSCode launched from the GUI does not inherit your
  shell `PATH`). Set it to an absolute path to override. It must be an **executable**, not a script:
  to run from source, use `"python"` with `code-ai.args` set to `["-m", "code_ai"]`.
- `code-ai.args`: extra arguments inserted before `bridge` (e.g. `["--config", "/path/config.json"]`).

The bridge runs with the first workspace folder as its cwd.

## Install from VSIX

```bash
scripts/build.sh
```

`scripts\build.bat` on Windows. No arguments and no preparation: it installs the npm
dependencies if they are missing, freezes the backend if it is not already built, and writes
`vsix/code-ai-<version>-<target>.vsix` for every platform it can serve. Install the result with
**"Extensions: Install from VSIX…"** or `code --install-extension <file>`.

**The package is self-contained.** It carries the compiled bundles *and* the frozen `code-ai`
backend, so a first-time install needs nothing else on the machine — no source checkout, no Python,
no `pip`, no virtualenv, no `PATH` entry. One package per platform, because the backend is most of
the download and a universal one would ship every user a binary they cannot run.

The backend is frozen from `packaging/code-ai.spec` into `.build-venv`, separate from your own
`.venv`, and reused on later runs while it is newer than `src/` (`--refresh-backend` forces it).
Building the **other** platform's binary needs Docker; without it that target is skipped with a
reason, and the `build-binaries` workflow produces one to pass with `--win`/`--linux`. Run
`scripts/build.sh --help` for the full list.

`scripts/verify-vsix.mjs` runs on every package and fails the build if the backend is missing, is
the wrong platform's, or would unpack without the executable bit — the three ways to produce a VSIX
that installs cleanly and then cannot start.

## Troubleshooting

**The panel is blank (the view opens but nothing renders).** This is almost always VSCode's webview
renderer, not the extension — common on Linux with hardware acceleration enabled. Confirm by opening a
Markdown preview (`Ctrl+Shift+V`): if that is also blank, disable hardware acceleration via
**"Preferences: Configure Runtime Arguments"** → set `"disable-hardware-acceleration": true` and fully
restart VSCode (equivalently, launch with `code --disable-gpu`).

## Keyboard

- **Cmd/Ctrl+Enter** in the composer sends the prompt.
