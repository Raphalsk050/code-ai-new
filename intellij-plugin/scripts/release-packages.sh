#!/bin/sh
# The release pipeline's two steps for this plugin, kept here so the two sets
# of jobs in .github/workflows/build-binaries.yml - the company runner's and
# GitHub's - run the same thing.
#
#   release-packages.sh stage OUT VERSION TARGET...   lay out each backend (needs Node)
#   release-packages.sh build OUT VERSION TARGET...   build each package (needs Java 21)
#
# OUT holds the backends the pipeline built - code-ai-linux-vX.Y.Z,
# code-ai-win-vX.Y.Z.exe, code-ai-mac-arm64/, code-ai-mac-x64/ - and gets the
# packages, as OUT/plugin/TARGET/code-ai-android-studio-vX.Y.Z-TARGET.zip.
#
# CODE_AI_PANEL_BUNDLED, when set, says vscode-extension/dist holds the panel
# already: a build with no Node has Gradle take it as it is.
# CODE_AI_TRUST_NETWORK, when set, is for a network that re-signs TLS.
set -eu

[ $# -ge 4 ] || {
  echo "usage: release-packages.sh stage|build OUT VERSION TARGET..." >&2
  exit 2
}
step=$1
out=$(cd "$2" && pwd)
version=$3
shift 3
plugin=$(cd "$(dirname "$0")/.." && pwd)
cd "$plugin"

# Where Gradle downloads from: itself, its plugins, the libraries, the IDE.
HOSTS="services.gradle.org github.com release-assets.githubusercontent.com
  plugins.gradle.org repo.maven.apache.org cache-redirector.jetbrains.com
  artifacts-caching-proxy.aws.intellij.net redirector.gvt1.com dl.google.com"

# Java has no switch for a network that re-signs TLS, as pip and npm have.
# Behind one, Gradle is given a copy of the usual certificates with the ones
# those hosts present here added to it: the nearest Java comes to not checking.
trust_network() {
  [ -n "${CODE_AI_TRUST_NETWORK:-}" ] || return 0
  java_home=${JAVA_HOME:-$(dirname "$(dirname "$(readlink -f "$(command -v java)")")")}
  scratch=$(mktemp -d)
  store=$scratch/cacerts
  cp "$java_home/lib/security/cacerts" "$store"
  chmod u+w "$store"
  for host in $HOSTS; do
    # A host that does not answer has nothing to add, and Gradle will say so.
    keytool -printcert -rfc -sslserver "$host" \
      -J-Dsun.net.client.defaultConnectTimeout=15000 \
      -J-Dsun.net.client.defaultReadTimeout=15000 2>/dev/null || true
  done | awk -v prefix="$scratch/presented" '
    /-----BEGIN CERTIFICATE-----/ { if (file) close(file); n += 1; file = prefix "-" n ".pem" }
    file { print > file }
  '
  added=0
  for pem in "$scratch"/presented-*.pem; do
    [ -f "$pem" ] || continue
    keytool -importcert -noprompt -alias "$(basename "$pem" .pem)" \
      -file "$pem" -keystore "$store" -storepass changeit >/dev/null 2>&1 || continue
    added=$((added + 1))
  done
  echo "Trusting the $added certificates this network presents"
  JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:-} -Djavax.net.ssl.trustStore=$store -Djavax.net.ssl.trustStorePassword=changeit"
  export JAVA_TOOL_OPTIONS
}

gradle_args=""
case "$step" in
  stage) ;;
  build)
    trust_network
    if [ -n "${CODE_AI_PANEL_BUNDLED:-}" ]; then
      [ -f ../vscode-extension/dist/webview.js ] || {
        echo "CODE_AI_PANEL_BUNDLED is set and vscode-extension/dist/webview.js is not there" >&2
        exit 1
      }
      gradle_args="-x installWebviewDependencies -x buildWebview"
    fi
    ;;
  *)
    echo "unknown step: $step (expected stage or build)" >&2
    exit 2
    ;;
esac

for target in "$@"; do
  staged="$plugin/build/release-backend/$target"
  case "$target" in
    linux-x64) from="$out/code-ai-linux-v$version" backend=code-ai ;;
    win32-x64) from="$out/code-ai-win-v$version.exe" backend=code-ai.exe ;;
    darwin-arm64) from="$out/code-ai-mac-arm64" backend=runtime.tar.gz ;;
    darwin-x64) from="$out/code-ai-mac-x64" backend=runtime.tar.gz ;;
    *)
      echo "unknown target: $target" >&2
      exit 2
      ;;
  esac
  if [ "$step" = stage ]; then
    node scripts/stage-backend.mjs --target "$target" --out "$staged" --from "$from"
  else
    # Unquoted on purpose: gradle_args is a list of arguments.
    # shellcheck disable=SC2086
    ./gradlew --no-daemon buildPlugin "-Ptarget=$target" "-PbackendDir=$staged" $gradle_args
    built="build/distributions/code-ai-intellij-$version-$target.zip"
    # Gradle packs a folder that is not there as gladly as one that is.
    jar tf "$built" | grep -q "/bin/$backend\$" || {
      echo "$built carries no backend: there is no bin/$backend in it" >&2
      exit 1
    }
    mkdir -p "$out/plugin/$target"
    cp "$built" "$out/plugin/$target/code-ai-android-studio-v$version-$target.zip"
  fi
done
