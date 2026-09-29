#!/bin/sh
# Code-AI for macOS. Run it from the Terminal with sh:
#
#     sh ~/Downloads/code-ai-mac-__ARCH__-v__VERSION__.sh
#
# It puts the `code-ai` command on your PATH and, when VS Code is installed,
# installs the Code-AI extension too. Run a newer one the same way to update.
#
#   --no-vscode   leave VS Code alone
#   --quiet       print only errors (what /update runs it with)
#
# The VS Code extension package rides at the end of this file; the command is
# unpacked from it. Started with sh, nothing here goes past Gatekeeper, and
# what it writes carries no quarantine.
set -eu

VERSION="__VERSION__"
ARCH="__ARCH__"
APP="$HOME/.code-ai/app"
BIN="$HOME/.local/bin"

vscode=1
quiet=0
for arg in "$@"; do
  case $arg in
    --no-vscode) vscode=0 ;;
    --quiet) quiet=1 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done
say() { [ "$quiet" = 1 ] || printf '%s\n' "$*"; }

if [ "$(uname -s)" != Darwin ]; then
  echo "This installer is for macOS." >&2
  exit 1
fi
if [ "$ARCH" = arm64 ] && [ "$(uname -m)" != arm64 ]; then
  echo "This Mac has an Intel chip: use code-ai-mac-x64-v$VERSION.sh instead." >&2
  exit 1
fi
if [ "$ARCH" = x64 ] && [ "$(uname -m)" = arm64 ]; then
  say "This Mac has an Apple chip: code-ai-mac-arm64-v$VERSION.sh runs faster than this one."
fi

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
line=$(awk '/^__CODE_AI_PAYLOAD__$/ { print NR + 1; exit }' "$0")
tail -n +"$line" "$0" > "$scratch/code-ai.vsix"
(cd "$scratch" && unzip -q code-ai.vsix 'extension/bin/*')

# One folder per version, current a link to the newest. The launcher resolves
# the link as it starts, so a Code-AI running the old version (the one /update
# is replacing) keeps its own files: that folder stays until the next install.
mkdir -p "$APP"
dest="$APP/v$VERSION"
rm -rf "$dest.partial"
mv "$scratch/extension/bin" "$dest.partial"
xattr -dr com.apple.quarantine "$dest.partial" 2>/dev/null || true
running=$(readlink "$APP/current" 2>/dev/null || true)
for old in "$APP"/v*; do
  name=${old##*/}
  if [ "$name" = "v$VERSION.partial" ]; then continue; fi
  # The same version reinstalled is replaced, running or not.
  if [ "$name" = "$running" ] && [ "$name" != "v$VERSION" ]; then continue; fi
  rm -rf "$old"
done
mv "$dest.partial" "$dest"
ln -sfn "v$VERSION" "$APP/current"

mkdir -p "$BIN"
ln -sf "$APP/current/code-ai" "$BIN/code-ai"
new_shell=0
case ":$PATH:" in
  *":$BIN:"*) ;;
  *)
    profile="$HOME/.zprofile"
    if [ "${SHELL##*/}" = bash ]; then profile="$HOME/.bash_profile"; fi
    entry='export PATH="$HOME/.local/bin:$PATH"'
    if ! grep -qsF "$entry" "$profile"; then
      printf '\n# Code-AI\n%s\n' "$entry" >> "$profile"
    fi
    new_shell=1
    ;;
esac

if [ "$vscode" = 1 ]; then
  code=$(command -v code 2>/dev/null || true)
  for app in "/Applications/Visual Studio Code.app" "$HOME/Applications/Visual Studio Code.app"; do
    if [ -z "$code" ] && [ -x "$app/Contents/Resources/app/bin/code" ]; then
      code="$app/Contents/Resources/app/bin/code"
    fi
  done
  if [ -z "$code" ]; then
    say "VS Code was not found, so only the command was installed."
  elif "$code" --install-extension "$scratch/code-ai.vsix" --force >/dev/null 2>&1; then
    say "VS Code: the Code-AI extension is installed; reload VS Code to start it."
  else
    say "VS Code refused the extension; install it from the release's .vsix instead."
  fi
fi

say ""
say "Code-AI v$VERSION is installed."
if [ "$new_shell" = 1 ]; then
  say "Open a new Terminal window, then run: code-ai"
else
  say "Run: code-ai"
fi
exit 0
__CODE_AI_PAYLOAD__
