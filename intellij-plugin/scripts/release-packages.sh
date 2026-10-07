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
# CODE_AI_CA_BUNDLE, when set, names a PEM file of certificates for Java to
# trust beside its own: the runner's, behind a proxy that re-signs TLS.
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

# Behind a proxy that re-signs TLS, Java knows none of the certificates it is
# shown: pip can be told to look away, Java cannot. It is handed the ones the
# runner itself trusts instead, beside its own (scripts/BuildNetwork.java).
trust_bundle() {
  bundle=${CODE_AI_CA_BUNDLE:-}
  [ -n "$bundle" ] || return 0
  [ -f "$bundle" ] || {
    echo "CODE_AI_CA_BUNDLE names $bundle, which is not a file" >&2
    exit 2
  }
  store=$(mktemp -d)/trusted.p12
  java scripts/BuildNetwork.java trust "$bundle" "$store"
  JAVA_TOOL_OPTIONS="${JAVA_TOOL_OPTIONS:-} -Djavax.net.ssl.trustStore=$store -Djavax.net.ssl.trustStorePassword=changeit -Djavax.net.ssl.trustStoreType=PKCS12"
  export JAVA_TOOL_OPTIONS
}

# What the build's first downloads answer from here, said before Gradle asks:
# when it is the network that says no, this is where it says why.
report_network() {
  gradle=$(sed -n 's/^distributionUrl=//p' gradle/wrapper/gradle-wrapper.properties | sed 's/\\:/:/')
  platform=$(sed -n 's/.*"org.jetbrains.intellij.platform") version "\(.*\)".*/\1/p' build.gradle.kts)
  portal=https://plugins.gradle.org/m2/org/jetbrains/intellij/platform/intellij-platform-gradle-plugin
  jetbrains=https://cache-redirector.jetbrains.com/www.jetbrains.com/intellij-repository/releases
  echo "The network, as this build finds it:"
  java scripts/BuildNetwork.java report \
    "Gradle=$gradle" \
    "its plugins=$portal/$platform/intellij-platform-gradle-plugin-$platform.pom" \
    "the libraries=https://repo.maven.apache.org/maven2/org/jetbrains/kotlin/kotlin-stdlib/maven-metadata.xml" \
    "JetBrains' own=$jetbrains/com/jetbrains/intellij/java/java-compiler-ant-tasks/maven-metadata.xml" \
    "the IDE=https://redirector.gvt1.com/edgedl/android/studio/patches/updates.xml" || true
}

gradle_args=""
case "$step" in
  stage) ;;
  build)
    trust_bundle
    report_network
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
