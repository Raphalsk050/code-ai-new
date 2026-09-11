# Packaging

Builds a single self-contained `code-ai` executable that bundles the Python
runtime and every dependency. End users run it directly — no Python, no `pip`,
no install step.

## Files

- `code-ai.spec` — PyInstaller spec for a one-file binary. Used for both Linux
  and Windows; it branches only where the platforms differ (e.g. it drops the
  POSIX-only `pexpect`/`ptyprocess` on Windows).
- `code_ai_launcher.py` — the frozen entry point; forwards to
  `code_ai.cli.main:main`.

## CI

`.github/workflows/build-binaries.yml` produces both binaries on the closed
self-hosted Linux runner (`code-linux`). It runs on `workflow_dispatch`, on
pushes to `feature/chat-improvement`, and on `v*` tags, uploading two
artifacts: `code-ai-linux-x86_64` and `code-ai-windows-x86_64`.

There is no Windows runner, so the Windows `.exe` is **cross-built on Linux**:
PyInstaller cannot cross-compile, so it is run against a real Windows Python
interpreter under Wine (the `tobix/pywine` image). The only runner requirement
is Docker.

## Building locally

Linux (or any host, native target):

```bash
python -m venv .build-venv && . .build-venv/bin/activate
pip install ".[browser]" "pyinstaller>=6,<7"
pyinstaller --clean --noconfirm packaging/code-ai.spec
./dist/code-ai --help
./dist/code-ai doctor browser --no-launch
```

Windows binary on a Linux host (needs Docker):

```bash
docker run --rm -v "$PWD":/work -w /work tobix/pywine:3.12 bash -euxc '
  wine python -m pip install ".[browser]" "pyinstaller>=6,<7"
  wine python -m PyInstaller --clean --noconfirm packaging/code-ai.spec
'
# -> dist/code-ai.exe
```

## The browser

The browser tools need Playwright, so the project is installed with its
`browser` extra, and the spec refuses to build without it. The binary carries
Playwright and its Node driver. It does **not** carry Chromium (hundreds of
MB, pinned per Playwright release): on first use the app downloads it into
the user's cache (`%LOCALAPPDATA%\ms-playwright`, `~/.cache/ms-playwright`).
When that download is impossible, it drives an installed Edge or Chrome
instead. On Linux it also installs the system libraries Chromium needs, when
that takes no password (running as root, or passwordless sudo); otherwise
`code-ai doctor browser --with-deps` installs them and asks for it. Every
download skips certificate checks unless `ssl_verification` is on.

```bash
code-ai doctor browser              # start it the way the agent does; say what is missing
code-ai doctor browser --install    # download Chromium first
code-ai doctor browser --no-launch  # only check Playwright and its driver (CI smoke test)
```

For an offline machine, copy the `ms-playwright` folder from one that has it,
or set `browser.channel` to `msedge` or `chrome` in the config.
