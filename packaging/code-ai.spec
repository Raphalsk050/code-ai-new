# PyInstaller spec for a single self-contained `code-ai` executable.
#
# Builds a one-file binary that bundles the Python runtime and every
# dependency, so end users run it without installing anything. The same spec
# drives the native Linux build and the Windows build (PyInstaller invoked
# under Wine), branching only where the two platforms genuinely differ.
#
# Build:  pyinstaller --clean --noconfirm packaging/code-ai.spec

import os
import sys

from PyInstaller.utils.hooks import (
    collect_all,
    collect_data_files,
    collect_submodules,
    copy_metadata,
)

# Paths must be anchored to the spec's own location, not the process cwd, so the
# build works no matter where PyInstaller is invoked from. SPECPATH is injected
# by PyInstaller and points at this file's directory (the repo's packaging/).
PROJECT_ROOT = os.path.dirname(SPECPATH)

datas = []
binaries = []
hiddenimports = []

# Third-party packages that ship data files and/or rely on dynamic imports
# PyInstaller cannot see statically (Textual stylesheets, `art` fonts,
# tiktoken's encodings, pydantic's compiled core).
for package in ("textual", "rich", "art", "tiktoken", "pydantic"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# tiktoken resolves encodings through the `tiktoken_ext` namespace package,
# which is loaded by name and therefore invisible to static analysis.
hiddenimports += collect_submodules("tiktoken_ext")
datas += collect_data_files("tiktoken_ext")

# The app reads its own packaged resources (banner.txt, theme.tcss) through
# importlib.resources, so they must be carried into the bundle.
datas += collect_data_files("code_ai")

# A few libraries read their installed distribution metadata at runtime.
for package in ("openai", "tiktoken", "textual"):
    try:
        datas += copy_metadata(package)
    except Exception:
        pass

excludes = ["tkinter"]
if sys.platform.startswith("win"):
    # pexpect/ptyprocess are POSIX-only and intentionally not installed on
    # Windows; persistent terminals degrade gracefully without them.
    excludes += ["pexpect", "ptyprocess"]

a = Analysis(
    [os.path.join(SPECPATH, "code_ai_launcher.py")],
    pathex=[os.path.join(PROJECT_ROOT, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="code-ai",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
)
