#!/usr/bin/env sh
# Builds the VSCode extension into installable VSIX packages.
#
# Each package carries the frozen `code-ai` backend, so installing it needs
# nothing else on the machine - no Python, no pip, no PATH entry. Nothing has
# to be prepared first: a binary that is not there is built, with PyInstaller
# for this platform and in a container for the other one.
set -eu

usage() {
    cat <<'USAGE'
Usage: scripts/build.sh [options]

Builds everything it can and says what it could not. No arguments needed.

  --target LIST       targets to build (default: both; e.g. linux-x64 or
                      win32-x64,linux-x64). A target named here must succeed.
  --win PATH          use this prebuilt Windows binary instead of building one
  --linux PATH        use this prebuilt Linux binary instead of building one
  --refresh-backend   rebuild the backend even if it looks up to date
  --verify-ssl        keep pip's certificate checks on
  --skip-verify       do not check the package afterwards (not recommended)

The backend is built from packaging/code-ai.spec into an isolated .build-venv,
and reused while it is newer than src/. Building the *other* platform's binary
needs Docker; without it that target is skipped, and the build-binaries
workflow produces one to pass with --win/--linux.
USAGE
}

for arg in "$@"; do
    case "$arg" in
        -h|--help) usage; exit 0 ;;
    esac
done

cd "$(dirname "$0")/.."

if ! command -v node >/dev/null 2>&1; then
    echo "node not found on PATH. Install Node 18 or newer." >&2
    exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
    echo "npm not found on PATH." >&2
    exit 1
fi

# vsce, esbuild and typescript live in devDependencies. The question is whether
# they resolve, not whether node_modules exists: a checkout from before one of
# them was added has the directory and not the package, and used to fail
# minutes later inside vsce with a bare MODULE_NOT_FOUND.
node scripts/ensure-deps.mjs

npm run typecheck
node scripts/package.mjs "$@"
