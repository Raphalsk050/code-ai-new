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
#
# playwright ships its Node driver as package data, so the whole package has
# to travel. Chromium does not - it is downloaded into the user's cache on
# first use.
#
# Fatal rather than best-effort: a build without it silently produced a binary
# that could never browse.
import importlib.util

if importlib.util.find_spec("playwright") is None:
    raise SystemExit(
        "playwright is not installed in the build environment, so the browser "
        "tools would be missing from the binary. Install the project into it: "
        "pip install ."
    )

# pywinpty is how persistent terminals work on Windows. It carries the whole
# ConPTY runtime as package data - conpty.dll, winpty.dll, winpty-agent.exe,
# OpenConsole.exe - and `from winpty import PtyProcess` happens inside a
# function, so static analysis reaches none of it. Without this the frozen
# binary opened a terminal that reported itself closed half a second later.
_WINDOWS_ONLY = ("winpty",) if sys.platform.startswith("win") else ()

# pypdfium2 carries the pdfium library as package data, reportlab its fonts,
# python-docx and python-pptx the blank templates every new file starts from.
for package in (
    *_WINDOWS_ONLY,
    "textual",
    "rich",
    "art",
    "tiktoken",
    "pydantic",
    "playwright",
    "pypdf",
    "pypdfium2",
    "pypdfium2_raw",
    "reportlab",
    "docx",
    "pptx",
):
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

# The C++ runtime has to match the C++ libraries next to it.
#
# A bundle can end up holding two toolchains. With a conda environment active,
# the stdlib's sqlite3 pulls in conda's ICU - built against a recent libstdc++ -
# while PyInstaller resolves libstdc++ itself through the system loader and
# collects the distro's, which on a long-term-support release is older. The
# bundled copy shadows every other, so the first `import sqlite3` dies with
#
#     libstdc++.so.6: version `CXXABI_1.3.15' not found
#         (required by .../libicui18n.so.78)
#
# Dropping the bundled copy does not help: the loader then finds the same old
# system one. What fixes it is taking the runtime from the directory ICU itself
# came from, which is by construction the toolchain ICU was built with.
if sys.platform.startswith("linux"):
    _icu = next(
        (
            source
            for dest, source, *_ in a.binaries
            if os.path.basename(dest).startswith("libicui18n")
        ),
        None,
    )
    if _icu:
        _beside_icu = os.path.join(
            os.path.dirname(os.path.realpath(_icu)), "libstdc++.so.6"
        )
        if os.path.exists(_beside_icu):
            a.binaries = [
                entry
                for entry in a.binaries
                if not os.path.basename(entry[0]).startswith("libstdc++.so")
            ]
            a.binaries.append(
                ("libstdc++.so.6", os.path.realpath(_beside_icu), "BINARY")
            )
            print(f"spec: bundling the C++ runtime beside ICU ({_beside_icu})")
        else:
            print(f"spec: no libstdc++ beside ICU ({os.path.dirname(_icu)}); leaving it alone")
    else:
        print("spec: ICU is not bundled, so the C++ runtime needs no help")

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
