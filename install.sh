#!/usr/bin/env sh
# Installs Code-AI from source into a virtualenv.
#
# The company network re-signs TLS with its own certificate, so pip is told to
# trust the PyPI hosts by default - the same two hosts the browser installer
# trusts (src/code_ai/tools/browser/install.py). Pass --verify-ssl on a network
# that leaves certificates alone.
set -eu

VENV=.venv
EXTRAS=dev
BROWSER=0
TRUSTED="--trusted-host pypi.org --trusted-host files.pythonhosted.org"

usage() {
    cat <<'USAGE'
Usage: install.sh [options]

  --extras LIST   extras to install (default: dev; e.g. dev,desktop)
  --venv DIR      virtualenv directory (default: .venv)
  --browser       download Chromium once the install is done
  --verify-ssl    keep pip's certificate checks on
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --extras) EXTRAS="$2"; shift 2 ;;
        --venv) VENV="$2"; shift 2 ;;
        --browser) BROWSER=1; shift ;;
        --verify-ssl) TRUSTED=""; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

cd "$(dirname "$0")"

# Newest first, and the version is checked rather than assumed: a "python3"
# that is 3.9 would fail much later, inside the build.
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3 python py; do
    command -v "$candidate" >/dev/null 2>&1 || continue
    "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1 || continue
    PYTHON="$candidate"
    break
done
if [ -z "$PYTHON" ]; then
    echo "Python 3.11+ not found on PATH." >&2
    exit 1
fi

VPY="$VENV/bin/python"
[ -d "$VENV" ] || "$PYTHON" -m venv "$VENV"
# Git Bash on Windows gets a venv laid out the Windows way.
[ -x "$VPY" ] || VPY="$VENV/Scripts/python.exe"

# $TRUSTED is unquoted on purpose: it is two flag pairs, or nothing at all.
"$VPY" -m pip install $TRUSTED --upgrade pip
"$VPY" -m pip install $TRUSTED -e ".[$EXTRAS]"

# Chromium is a few hundred MB and is normally fetched on first use; doing it
# here means the first browsing turn is not the one that waits for it.
[ "$BROWSER" -eq 0 ] || "$VPY" -m code_ai doctor browser --install

echo
echo "Installed. Activate with: . $(dirname "$VPY")/activate"
echo "Then run: code-ai"
