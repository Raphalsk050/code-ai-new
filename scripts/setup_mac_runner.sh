#!/bin/sh
# Registers this Mac as the GitHub Actions runner that checks Code-AI's macOS
# build before each release: the workflow's macos-check job installs it and
# starts it. The Linux runner assembles that build; only a Mac can run it.
#
#     sh scripts/setup_mac_runner.sh <repository URL> <token> [label]
#
#   repository URL  https://github.sec.samsung.net/<owner>/<repo>
#   token           that repository's Settings > Actions > Runners >
#                   New self-hosted runner (it expires in an hour)
#   label           code-mac unless given
#
# Last step, on the repository: Settings > Secrets and variables > Actions >
# Variables, CODE_AI_MAC_RUNNER = the label. Clearing it turns the check off.
#
# The runner is a launchd agent of whoever runs this, so it works while that
# user is logged in and the Mac awake; while it is off, releases wait for it.
# Nothing else is needed here: the check runs the build's own Python.
set -eu

url=${1:?"usage: sh scripts/setup_mac_runner.sh <repository URL> <token> [label]"}
token=${2:?"the registration token, from Settings > Actions > Runners"}
label=${3:-code-mac}

# Pinned with its checksum. If GHES wants the version its "New self-hosted
# runner" page shows, pass that page's version and SHA-256 in RUNNER_VERSION
# and RUNNER_SHA256.
version=${RUNNER_VERSION:-2.337.0}
case $(uname -m) in
  arm64)
    package=osx-arm64
    sha256=${RUNNER_SHA256:-5a2cd92908a93d7276a194e1de6008099f3e7946f3f8e14aa7a1a7b4a31fdec2}
    ;;
  x86_64)
    package=osx-x64
    sha256=${RUNNER_SHA256:-d383f505d7ed041b1873ab68c35dd766fc093f2252330f95bb427be8f2c6dcfc}
    ;;
  *)
    echo "Unsupported Mac: $(uname -m)" >&2
    exit 1
    ;;
esac
archive="actions-runner-$package-$version.tar.gz"
download=${RUNNER_DOWNLOAD:-https://github.com/actions/runner/releases/download/v$version/$archive}

dir="$HOME/actions-runner-code-ai"
mkdir -p "$dir"
cd "$dir"
if [ ! -x ./config.sh ]; then
  # --insecure: the company proxy re-signs TLS, and the checksum is the check.
  curl -fL --insecure -o "$archive" "$download"
  echo "$sha256  $archive" | shasum -a 256 -c -
  tar xzf "$archive"
  rm "$archive"
fi

# The runner reaches the company GitHub through the same proxy.
export GITHUB_ACTIONS_RUNNER_TLS_NO_VERIFY=1
if ! grep -qs '^GITHUB_ACTIONS_RUNNER_TLS_NO_VERIFY=' .env; then
  echo 'GITHUB_ACTIONS_RUNNER_TLS_NO_VERIFY=1' >> .env
fi

name="$(scutil --get LocalHostName 2>/dev/null || hostname -s)-code-ai"
./config.sh --unattended --replace --url "$url" --token "$token" --name "$name" --labels "$label"
./svc.sh install
./svc.sh start

# With Rosetta the same Mac checks the Intel build as well.
if [ "$(uname -m)" = arm64 ] && ! arch -x86_64 /usr/bin/true 2>/dev/null; then
  echo "Installing Rosetta, so the Intel build is checked too (it asks for your password)."
  sudo softwareupdate --install-rosetta --agree-to-license \
    || echo "Rosetta was not installed: the Intel build will not be checked."
fi

echo
echo "Runner $name is up with the label $label. Last step, on $url:"
echo "  Settings > Secrets and variables > Actions > Variables > New repository variable"
echo "  Name: CODE_AI_MAC_RUNNER    Value: $label"
