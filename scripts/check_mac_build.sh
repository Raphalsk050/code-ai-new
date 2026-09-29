#!/bin/sh
# Installs each macOS build the way a user does and starts it: what the
# workflow's macos-check job runs on a Mac, the one step no Linux runner can
# take. Each install goes into a throwaway home (with a space in its path, the
# hard case), so nothing of the runner user's changes.
#
#     sh scripts/check_mac_build.sh <version> [folder with the installers]
set -eu

version=${1:?"usage: sh scripts/check_mac_build.sh <version> [folder]"}
folder=${2:-out}
machine=$(uname -m)
checked=0
for arch in arm64 x64; do
  installer="$folder/code-ai-mac-$arch-v$version.sh"
  if [ ! -f "$installer" ]; then
    echo "::error::$installer is missing."
    exit 1
  fi
  if [ "$arch" = arm64 ] && [ "$machine" != arm64 ]; then
    echo "::notice::An Intel Mac cannot run the arm64 build, so it was not checked."
    continue
  fi
  if [ "$arch" = x64 ] && [ "$machine" = arm64 ] && ! arch -x86_64 /usr/bin/true 2>/dev/null; then
    # A hosted Mac takes Rosetta without a password; a company one got it at setup.
    sudo -n softwareupdate --install-rosetta --agree-to-license >/dev/null 2>&1 || true
    if ! arch -x86_64 /usr/bin/true 2>/dev/null; then
      echo "::notice::No Rosetta on this Mac, so the x64 build was not checked."
      continue
    fi
  fi
  scratch=$(mktemp -d)
  home="$scratch/check home"
  mkdir -p "$home"
  HOME="$home" SHELL=/bin/zsh sh "$installer" --no-vscode
  HOME="$home" "$home/.local/bin/code-ai" --help > /dev/null
  HOME="$home" "$home/.local/bin/code-ai" doctor browser --no-launch
  rm -rf "$scratch"
  echo "code-ai-mac-$arch installs and starts."
  checked=$((checked + 1))
done
if [ "$checked" = 0 ]; then
  echo "::error::No macOS build could be checked on this Mac."
  exit 1
fi
