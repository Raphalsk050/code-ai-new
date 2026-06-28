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
pip install . "pyinstaller>=6,<7"
pyinstaller --clean --noconfirm packaging/code-ai.spec
./dist/code-ai --help
```

Windows binary on a Linux host (needs Docker):

```bash
docker run --rm -v "$PWD":/work -w /work tobix/pywine:3.12 bash -euxc '
  wine python -m pip install . "pyinstaller>=6,<7"
  wine python -m PyInstaller --clean --noconfirm packaging/code-ai.spec
'
# -> dist/code-ai.exe
```
