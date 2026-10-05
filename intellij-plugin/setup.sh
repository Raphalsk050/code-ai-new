#!/usr/bin/env sh
# Sets a Linux machine up to build the Android Studio plugin and try it.
#
# It installs what is missing (a Java to start the build with, Node.js for the
# panel, Python for the backend, the libraries the sandbox IDE needs to show a
# window), installs the backend into the repository's .venv, points the build
# at the Android Studio that is installed, and builds the package once, with
# the backend inside it, which fetches the rest. Run it again whenever: what
# is already there is left alone.
#
# System packages come from the distribution's own package manager (apt, dnf or
# pacman), and nothing is installed before the command that does it is shown.
# Everything else stays in the repository's folder and in Gradle's.
set -eu

usage() {
    cat <<'USAGE'
Usage: ./setup.sh [options]

Gets this machine ready to build the plugin and try it. No arguments needed.

  --ide DIR       Android Studio to build against (default: the one it finds;
                  with none, the build downloads one, over a gigabyte)
  --no-backend    leave Python and the backend out: the package is built
                  without one, and looks for a `code-ai` of your own
  --no-build      stop before building the package
  -y, --yes       install system packages without asking
  --dry-run       say what would be done, and do nothing
  -h, --help      this text

What it sets up:
  - a Java to start the build with (the build finds or fetches the JDK 21 it
    runs on and compiles with)
  - Node.js 18 or newer, to bundle the panel: the distribution's, or one of
    its own in .tools/ where the distribution's is too old
  - Python 3.11 or newer: the backend in ../.venv for the sandbox IDE (the
    repository's install.sh), and the one frozen into the package
  - on Linux, the system libraries the sandbox IDE and its browser load
  - local.properties, naming the Android Studio to compile against
  - the package itself, backend inside:
    build/distributions/code-ai-intellij-<version>-<system>.zip
USAGE
}

IDE=""
BACKEND=1
BUILD=1
YES=0
DRY=0

while [ $# -gt 0 ]; do
    case "$1" in
        --ide)
            [ $# -ge 2 ] || { echo "--ide needs a folder" >&2; exit 2; }
            IDE="$2"; shift 2 ;;
        --no-backend) BACKEND=0; shift ;;
        --no-build) BUILD=0; shift ;;
        -y|--yes) YES=1; shift ;;
        --dry-run) DRY=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/.." && pwd)
SYSTEM=$(uname -s)
TOOLS="$HERE/.tools"
# The Node.js line fetched when the distribution has none that will do.
NODE_LINE=24
NODE_MINIMUM=18
TAB=$(printf '\t')

say() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# Run a command, or only show it under --dry-run.
run() {
    if [ "$DRY" -eq 1 ]; then
        say "    would run: $*"
    else
        "$@"
    fi
}

# Everything it builds and fetches would belong to root, and so would Gradle's own folder.
if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ]; then
    die "run this as yourself, without sudo: it asks for sudo itself, for the one command that needs it."
fi

# --- settings kept for the build ----------------------------------------------

# The value local.properties gives a key, or nothing.
local_setting() {
    [ -f "$HERE/local.properties" ] || return 1
    setting_value=$(sed -n "s/^$1[[:space:]]*=[[:space:]]*//p" "$HERE/local.properties" | head -n 1)
    [ -n "$setting_value" ] || return 1
    printf '%s\n' "$setting_value"
}

# Write a key of local.properties, leaving the others as they are.
set_local() {
    say "Writing $1 to local.properties"
    [ "$DRY" -eq 0 ] || return 0
    if [ -f "$HERE/local.properties" ]; then
        grep -v "^$1[[:space:]]*=" "$HERE/local.properties" > "$HERE/local.properties.tmp" || true
    else
        : > "$HERE/local.properties.tmp"
    fi
    printf '%s=%s\n' "$1" "$2" >> "$HERE/local.properties.tmp"
    mv "$HERE/local.properties.tmp" "$HERE/local.properties"
}

# --- what is there ------------------------------------------------------------

# The major version a `java` reports (8 for 1.8), or nothing when it does not run.
java_major() {
    "$1" -version 2>&1 | sed -n 's/.* version "1\.\([0-9][0-9]*\).*/\1/p; s/.* version "\([0-9][0-9]*\).*/\1/p' | head -n 1
}

# The `java` ./gradlew starts with: JAVA_HOME's when that is set, as the wrapper has it, else the one on PATH.
starter_java() {
    if [ -n "${JAVA_HOME:-}" ]; then
        [ -x "$JAVA_HOME/bin/java" ] || return 1
        printf '%s\n' "$JAVA_HOME/bin/java"
    else
        command -v java
    fi
}

# Whether a JDK 21 is where Gradle looks for one by itself: the build runs on it and compiles with it.
jdk21_present() {
    for jdk_home in "${JAVA_HOME:-}" /usr/lib/jvm/* /usr/lib64/jvm/* /usr/java/* /opt/java/* \
        "$HOME"/.gradle/jdks/* "$HOME"/.gradle/jdks/*/Contents/Home "$HOME"/.jdks/* "$HOME"/.sdkman/candidates/java/*; do
        [ -n "$jdk_home" ] && [ -x "$jdk_home/bin/javac" ] || continue
        if grep -q '^JAVA_VERSION="21' "$jdk_home/release" 2>/dev/null; then
            return 0
        fi
    done
    return 1
}

# The major version of a `node`, or nothing.
node_major() {
    "$1" --version 2>/dev/null | sed -n 's/^v\([0-9][0-9]*\).*/\1/p'
}

# Whether a Node.js will do: new enough, and with the npm that came with it.
node_usable() {
    node_version=$(node_major "$1" || true)
    [ -n "$node_version" ] && [ "$node_version" -ge "$NODE_MINIMUM" ] && [ -x "$2" ]
}

# The newest Python that is 3.11 or newer, as install.sh picks it.
find_python() {
    for python_name in python3.13 python3.12 python3.11 python3 python; do
        have "$python_name" || continue
        "$python_name" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' >/dev/null 2>&1 || continue
        printf '%s\n' "$python_name"
        return 0
    done
    return 1
}

python_has_venv() {
    "$1" -c 'import ensurepip, venv' >/dev/null 2>&1
}

# The product-info.json of an IDE folder, wherever the platform keeps it.
product_info() {
    for info_file in "$1/product-info.json" "$1/Contents/Resources/product-info.json"; do
        if [ -f "$info_file" ]; then
            printf '%s\n' "$info_file"
            return 0
        fi
    done
    return 1
}

# The IntelliJ Platform release an IDE is, as its builds are numbered: the 253 of 253.30387.90.
ide_branch() {
    ide_info=$(product_info "$1") || return 1
    sed -n 's/.*"buildNumber"[^"]*"\([0-9][0-9]*\)\..*/\1/p' "$ide_info" | head -n 1
}

# Where a command on PATH really lives, links followed.
real_path() {
    command_path=$(command -v "$1") || return 1
    if have realpath; then
        realpath "$command_path"
    elif have readlink && readlink -f "$command_path" >/dev/null 2>&1; then
        readlink -f "$command_path"
    else
        printf '%s\n' "$command_path"
    fi
}

# Every Android Studio on this machine, in the places it is usually put: one folder a line.
android_studios() {
    set -- \
        "/opt/android-studio" \
        "/usr/local/android-studio" \
        "$HOME/android-studio" \
        "$HOME/.local/share/android-studio" \
        "$HOME/.local/share/JetBrains/Toolbox/apps/android-studio" \
        "$HOME/.local/share/umake/android/android-studio" \
        "/snap/android-studio/current/android-studio" \
        "/snap/android-studio/current" \
        "/var/lib/flatpak/app/com.google.AndroidStudio/current/active/files/extra/android-studio" \
        "$HOME/.local/share/flatpak/app/com.google.AndroidStudio/current/active/files/extra/android-studio" \
        "/Applications/Android Studio.app" \
        "$HOME/Applications/Android Studio.app"
    # Its launcher on PATH: the IDE is the folder above bin/.
    for launcher in studio.sh studio android-studio; do
        if have "$launcher"; then
            launcher_path=$(real_path "$launcher" || true)
            if [ -n "$launcher_path" ]; then
                set -- "$@" "$(dirname "$(dirname "$launcher_path")")"
            fi
        fi
    done
    for studio_dir in "$@" /opt/android-studio-* "$HOME"/.local/share/JetBrains/Toolbox/apps/AndroidStudio/ch-*/*; do
        if product_info "$studio_dir" >/dev/null 2>&1; then
            printf '%s\n' "$studio_dir"
        fi
    done
}

# The libraries the sandbox IDE and its embedded browser load from the system,
# as the Linux runtime's own binaries name them, and the package each comes in:
#   library | apt | dnf | pacman
# Where a release renamed a package both names are given, the newer first.
LIBRARIES='libX11.so.6|libx11-6|libX11|libx11
libXcomposite.so.1|libxcomposite1|libXcomposite|libxcomposite
libXdamage.so.1|libxdamage1|libXdamage|libxdamage
libXext.so.6|libxext6|libXext|libxext
libXfixes.so.3|libxfixes3|libXfixes|libxfixes
libXi.so.6|libxi6|libXi|libxi
libXrandr.so.2|libxrandr2|libXrandr|libxrandr
libXrender.so.1|libxrender1|libXrender|libxrender
libXtst.so.6|libxtst6|libXtst|libxtst
libasound.so.2|libasound2t64 libasound2|alsa-lib|alsa-lib
libatk-1.0.so.0|libatk1.0-0t64 libatk1.0-0|atk at-spi2-core|at-spi2-core
libatk-bridge-2.0.so.0|libatk-bridge2.0-0t64 libatk-bridge2.0-0|at-spi2-atk at-spi2-core|at-spi2-core
libatspi.so.0|libatspi2.0-0t64 libatspi2.0-0|at-spi2-core|at-spi2-core
libcairo.so.2|libcairo2|cairo|cairo
libcups.so.2|libcups2t64 libcups2|cups-libs|libcups
libdbus-1.so.3|libdbus-1-3|dbus-libs|dbus
libexpat.so.1|libexpat1|expat|expat
libfontconfig.so.1|libfontconfig1|fontconfig|fontconfig
libfreetype.so.6|libfreetype6|freetype|freetype2
libgbm.so.1|libgbm1|mesa-libgbm|mesa
libglib-2.0.so.0|libglib2.0-0t64 libglib2.0-0|glib2|glib2
libnspr4.so|libnspr4|nspr|nspr
libnss3.so|libnss3|nss|nss
libpango-1.0.so.0|libpango-1.0-0|pango|pango
libudev.so.1|libudev1|systemd-libs|systemd-libs
libxcb.so.1|libxcb1|libxcb|libxcb
libxkbcommon.so.0|libxkbcommon0|libxkbcommon|libxkbcommon'

# The lines of that table whose library the system does not have.
missing_libraries() {
    [ "$SYSTEM" = "Linux" ] || return 0
    linker_cache=""
    for linker in ldconfig /sbin/ldconfig /usr/sbin/ldconfig; do
        if have "$linker"; then
            linker_cache=$("$linker" -p 2>/dev/null || true)
            break
        fi
    done
    # Without the linker's list there is nothing to hold the table against.
    [ -n "$linker_cache" ] || return 0
    while IFS='|' read -r library_name library_rest; do
        [ -n "$library_name" ] || continue
        case "$linker_cache" in
            *"$TAB$library_name "*) ;;
            *) printf '%s|%s\n' "$library_name" "$library_rest" ;;
        esac
    done <<TABLE
$LIBRARIES
TABLE
}

# --- the package manager ------------------------------------------------------

MANAGER=""
if [ "$SYSTEM" = "Linux" ]; then
    if have apt-get; then MANAGER=apt
    elif have dnf; then MANAGER=dnf
    elif have pacman; then MANAGER=pacman
    fi
fi

SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo "; fi

# What dnf can install, of the names this script may ask it about: asked once, each question reads every repository.
DNF_NAMES="java-21-openjdk-devel java-25-openjdk-devel java-latest-openjdk-devel nodejs npm python3 curl tar \
libX11 libXcomposite libXdamage libXext libXfixes libXi libXrandr libXrender libXtst alsa-lib atk at-spi2-atk \
at-spi2-core cairo cups-libs dbus-libs expat fontconfig freetype mesa-libgbm glib2 nspr nss pango systemd-libs \
libxcb libxkbcommon"
DNF_KNOWN=""

# The version apt would install of a package, or nothing: a name only other packages provide has none.
apt_candidate() {
    apt-cache policy "$1" 2>/dev/null | sed -n 's/^ *Candidate: *//p' | head -n 1 | grep -v '^(none)$' || true
}

# Whether the package manager has a package to install under this name.
package_known() {
    case "$MANAGER" in
        apt) [ -n "$(apt_candidate "$1")" ] ;;
        dnf) printf '%s\n' "$DNF_KNOWN" | grep -q "^$1-[0-9]" ;;
        pacman) pacman -Si "$1" >/dev/null 2>&1 ;;
        *) return 1 ;;
    esac
}

# The first of the names given that the package manager has, or nothing.
first_known() {
    for package_name in "$@"; do
        if package_known "$package_name"; then
            printf '%s\n' "$package_name"
            return 0
        fi
    done
    return 1
}

# The same, for a package the script cannot go on without: when none of the
# names can be confirmed the first is tried anyway, and the install says so.
first_known_or_first() {
    first_known "$@" || printf '%s\n' "$1"
}

PACKAGES=""
LISTS_FETCHED=0
want() {
    for wanted in "$@"; do
        case " $PACKAGES " in
            *" $wanted "*) ;;
            *) PACKAGES="$PACKAGES $wanted" ;;
        esac
    done
}

# apt has names to install only once it has fetched its lists: a system fresh from an image has none.
apt_lists_present() {
    for list_file in /var/lib/apt/lists/*Packages*; do
        [ -e "$list_file" ] && return 0
    done
    return 1
}

# --- Node.js of its own -------------------------------------------------------

# download URL FILE [quiet]: with the progress of a large file shown, unless told not to.
download() {
    if have curl; then
        if [ -n "${3:-}" ]; then
            curl -fsSL --retry 2 -o "$2" "$1"
        else
            curl -fL --retry 2 --progress-bar -o "$2" "$1"
        fi
    elif have wget; then
        wget -q -O "$2" "$1"
    else
        return 1
    fi
}

sha256_of() {
    if have sha256sum; then
        sha256sum "$1" | cut -d ' ' -f 1
    elif have shasum; then
        shasum -a 256 "$1" | cut -d ' ' -f 1
    else
        return 1
    fi
}

# Fetches Node.js from nodejs.org into .tools/, checked against the sums
# published beside it, and points the build at it. Nothing of the system's is
# touched: only this build runs it.
fetch_node() {
    case "$SYSTEM:$(uname -m)" in
        Linux:x86_64|Linux:amd64) node_platform=linux-x64 ;;
        Linux:aarch64|Linux:arm64) node_platform=linux-arm64 ;;
        Darwin:arm64) node_platform=darwin-arm64 ;;
        Darwin:x86_64) node_platform=darwin-x64 ;;
        *) die "nodejs.org has no Node.js to fetch for this machine ($SYSTEM $(uname -m)). Install Node.js $NODE_MINIMUM or newer yourself and run this again." ;;
    esac
    if ! have curl && ! have wget; then
        die "fetching Node.js needs curl or wget. Install one, or Node.js $NODE_MINIMUM or newer itself, and run this again."
    fi
    have tar || die "unpacking Node.js needs tar. Install it and run this again."
    node_site="https://nodejs.org/dist/latest-v$NODE_LINE.x"
    say "Fetching Node.js $NODE_LINE for this build into $TOOLS (from nodejs.org)"
    if [ "$DRY" -eq 1 ]; then
        say "    would fetch: $node_site/node-v$NODE_LINE.*-$node_platform.tar.gz"
        return 0
    fi
    mkdir -p "$TOOLS"
    download "$node_site/SHASUMS256.txt" "$TOOLS/node.sums" quiet \
        || die "could not reach nodejs.org. On a network that re-signs TLS, give the system that network's certificate, or install Node.js $NODE_MINIMUM or newer yourself, and run this again."
    node_line=$(grep " node-v[0-9.]*-$node_platform\.tar\.gz\$" "$TOOLS/node.sums" | head -n 1 || true)
    [ -n "$node_line" ] || die "nodejs.org lists no Node.js $NODE_LINE for $node_platform."
    node_sum=${node_line%% *}
    node_archive=${node_line##* }
    node_name=${node_archive%.tar.gz}
    if ! node_usable "$TOOLS/$node_name/bin/node" "$TOOLS/$node_name/bin/npm"; then
        download "$node_site/$node_archive" "$TOOLS/$node_archive" || die "could not download $node_site/$node_archive"
        node_actual=$(sha256_of "$TOOLS/$node_archive" || true)
        if [ "$node_actual" != "$node_sum" ]; then
            rm -f "$TOOLS/$node_archive"
            die "$node_archive is not the file nodejs.org published (its sum is ${node_actual:-unknown, there is no sha256sum here}): nothing was unpacked."
        fi
        rm -rf "${TOOLS:?}/$node_name"
        tar -xzf "$TOOLS/$node_archive" -C "$TOOLS"
        rm -f "$TOOLS/$node_archive"
    fi
    rm -f "$TOOLS/node.sums"
    # Checked rather than assumed: a build for another C library unpacks and does not run.
    node_usable "$TOOLS/$node_name/bin/node" "$TOOLS/$node_name/bin/npm" \
        || die "the Node.js from nodejs.org does not run on this system ($TOOLS/$node_name). Install Node.js $NODE_MINIMUM or newer yourself and run this again."
    say "Node.js: $("$TOOLS/$node_name/bin/node" --version), in $TOOLS/$node_name"
    if [ "$(local_setting nodeDir || true)" != "$TOOLS/$node_name" ]; then
        set_local nodeDir "$TOOLS/$node_name"
    fi
}

# --- what this machine has ----------------------------------------------------

step "Checking what this machine has"

if [ -n "${JAVA_HOME:-}" ] && [ ! -x "$JAVA_HOME/bin/java" ]; then
    die "JAVA_HOME is $JAVA_HOME, which has no bin/java, and the build starts with JAVA_HOME's when it is set. Unset it or point it at a JDK, and run this again."
fi

NEED_JAVA=0
STARTER=$(starter_java || true)
STARTER_VERSION=""
if [ -n "$STARTER" ]; then STARTER_VERSION=$(java_major "$STARTER" || true); fi
if [ -z "$STARTER_VERSION" ]; then
    NEED_JAVA=1
    say "Java: none to start the build with"
elif jdk21_present; then
    say "Java: $STARTER_VERSION starts the build, and a JDK 21 is installed for it to run on"
else
    say "Java: $STARTER_VERSION starts the build, which fetches the JDK 21 it runs on (about 200 MB, kept by Gradle)"
fi

# Where the panel's Node.js comes from: the system, .tools/, or nowhere yet.
NODE_FROM=""
OWN_NODE=$(local_setting nodeDir || true)
if [ -n "$OWN_NODE" ] && node_usable "$OWN_NODE/bin/node" "$OWN_NODE/bin/npm"; then
    NODE_FROM=own
    say "Node.js: $("$OWN_NODE/bin/node" --version), the build's own in $OWN_NODE"
elif have node && have npm && node_usable "$(command -v node)" "$(command -v npm)"; then
    NODE_FROM=system
    say "Node.js: $(node --version), npm $(npm --version 2>/dev/null || echo "?")"
elif have node; then
    say "Node.js: $(node --version 2>/dev/null || echo "one that does not run")$(have npm || printf ', without npm'), and bundling the panel needs $NODE_MINIMUM or newer with npm"
else
    say "Node.js: not found"
fi

# The backend is Python: installed into .venv for the sandbox IDE, and frozen into the package.
NEED_PYTHON=0
NEED_BINUTILS=0
PYTHON=""
if [ "$BACKEND" -eq 1 ]; then
    PYTHON=$(find_python || true)
    if [ -z "$PYTHON" ]; then
        NEED_PYTHON=1
        say "Python: none that is 3.11 or newer, which the backend needs"
    elif ! python_has_venv "$PYTHON"; then
        NEED_PYTHON=1
        say "Python: $("$PYTHON" --version 2>&1), without its venv module"
    else
        say "Python: $("$PYTHON" --version 2>&1)"
    fi
    # Freezing the backend on Linux reads its libraries with objdump.
    if [ "$SYSTEM" = "Linux" ] && ! have objdump; then
        NEED_BINUTILS=1
        say "objdump: not found, and freezing the backend into the package needs it"
    fi
    if [ -x "$REPO/.venv/bin/code-ai" ]; then
        say "Backend for the sandbox IDE: already in $REPO/.venv"
    fi
fi

MISSING_LIBRARIES=$(missing_libraries)
if [ -n "$MISSING_LIBRARIES" ]; then
    say "Libraries the sandbox IDE loads, and are not here: $(printf '%s\n' "$MISSING_LIBRARIES" | cut -d '|' -f 1 | tr '\n' ' ')"
fi

# --- system packages ----------------------------------------------------------

# Whether Node.js is to be fetched into .tools/, and not asked of the distribution.
FETCH_NODE=0
if [ -z "$NODE_FROM" ]; then
    # A Node.js that is there and too old stays as it is: the system may be using it.
    if have node || [ -z "$MANAGER" ]; then FETCH_NODE=1; fi
fi

if [ -n "$MANAGER" ] && { [ "$NEED_JAVA" -eq 1 ] || [ -z "$NODE_FROM" ] || [ "$NEED_PYTHON" -eq 1 ] || [ "$NEED_BINUTILS" -eq 1 ] || [ -n "$MISSING_LIBRARIES" ]; }; then
    if [ "$MANAGER" = "apt" ] && ! apt_lists_present; then
        say "apt has no package lists yet (a system fresh from an image): fetching them, to know what it can install"
        # $SUDO is unquoted on purpose: it is a command, or nothing.
        # shellcheck disable=SC2086
        run ${SUDO}apt-get update
        LISTS_FETCHED=1
    fi
    if [ "$MANAGER" = "dnf" ]; then
        say "Asking dnf what it has (it reads the repositories' lists, which takes a moment the first time)"
        # $DNF_NAMES is unquoted on purpose: it is a list of names.
        # shellcheck disable=SC2086
        DNF_KNOWN=$(dnf -q repoquery $DNF_NAMES 2>/dev/null || true)
    fi

    if [ "$NEED_JAVA" -eq 1 ]; then
        case "$MANAGER" in
            apt) want "$(first_known_or_first openjdk-21-jdk-headless default-jdk-headless)" ;;
            dnf) want "$(first_known_or_first java-21-openjdk-devel java-25-openjdk-devel java-latest-openjdk-devel)" ;;
            pacman) want "$(first_known_or_first jdk21-openjdk jdk-openjdk)" ;;
        esac
    fi

    if [ -z "$NODE_FROM" ] && [ "$FETCH_NODE" -eq 0 ]; then
        if [ "$MANAGER" = "apt" ]; then
            # Debian and Ubuntu keep the Node.js a release came out with, and older releases have one too old.
            apt_node=$(apt_candidate nodejs | sed -n 's/^\([0-9]*:\)\{0,1\}\([0-9][0-9]*\).*/\2/p')
            if [ -n "$apt_node" ] && [ "$apt_node" -ge "$NODE_MINIMUM" ]; then
                want nodejs npm
            else
                say "This distribution's Node.js is ${apt_node:-not there}: the build gets one of its own."
                FETCH_NODE=1
            fi
        else
            want nodejs npm
        fi
    fi
    if [ "$FETCH_NODE" -eq 1 ]; then
        have curl || have wget || want curl
        have tar || want tar
    fi

    if [ "$NEED_PYTHON" -eq 1 ]; then
        case "$MANAGER" in
            apt)
                if [ -n "$PYTHON" ]; then
                    # The interpreter is there and its venv module is a package of its own.
                    python_series=$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')
                    want "$(first_known_or_first "python$python_series-venv" python3-venv)"
                elif apt_candidate python3 | grep -q '^\([0-9]*:\)\{0,1\}3\.1[1-9]'; then
                    want python3 python3-venv
                else
                    # A release whose python3 is older may carry a newer one beside it.
                    newer=$(first_known python3.13 python3.12 python3.11 || true)
                    if [ -n "$newer" ]; then want "$newer" "$newer-venv"; fi
                fi ;;
            dnf) want python3 ;;
            pacman) want python ;;
        esac
    fi
    if [ "$NEED_BINUTILS" -eq 1 ]; then want binutils; fi

    if [ -n "$MISSING_LIBRARIES" ]; then
        UNPLACED=""
        while IFS='|' read -r library_name for_apt for_dnf for_pacman; do
            [ -n "$library_name" ] || continue
            case "$MANAGER" in
                apt) candidates=$for_apt ;;
                dnf) candidates=$for_dnf ;;
                *) candidates=$for_pacman ;;
            esac
            # $candidates is unquoted on purpose: it is a list of names.
            # shellcheck disable=SC2086
            library_package=$(first_known $candidates || true)
            if [ -n "$library_package" ]; then
                want "$library_package"
            else
                UNPLACED="$UNPLACED $library_name"
            fi
        done <<TABLE
$MISSING_LIBRARIES
TABLE
    fi
fi

PACKAGES=${PACKAGES# }
INSTALLED=0
if [ -n "$PACKAGES" ]; then
    case "$MANAGER" in
        apt) INSTALL="apt-get install -y $PACKAGES" ;;
        dnf) INSTALL="dnf install -y $PACKAGES" ;;
        pacman) INSTALL="pacman -S --needed --noconfirm $PACKAGES" ;;
    esac

    step "Installing system packages"
    say "    $SUDO$INSTALL"
    PROCEED=1
    if [ "$DRY" -eq 0 ] && [ "$YES" -eq 0 ]; then
        PROCEED=0
        # Asked of the terminal, not of whatever feeds this script. Opening it is the only way to know there is one.
        if (: > /dev/tty) 2>/dev/null && (: < /dev/tty) 2>/dev/null; then
            printf 'Install them now? [Y/n] ' > /dev/tty
            read -r answer < /dev/tty || answer=n
            case "$answer" in
                ""|y|Y|yes|Yes|s|S|sim|Sim) PROCEED=1 ;;
            esac
        else
            say "Nobody to ask, and --yes was not given."
        fi
        if [ "$PROCEED" -eq 0 ] && { [ "$NEED_JAVA" -eq 1 ] || [ "$NEED_PYTHON" -eq 1 ] || [ "$NEED_BINUTILS" -eq 1 ]; }; then
            die "nothing was installed, and the build cannot go on without them. Install them with the command above, or run this again with --yes."
        fi
        [ "$PROCEED" -eq 1 ] || say "Nothing was installed: going on without them."
    fi
    if [ "$PROCEED" -eq 1 ]; then
        if [ -n "$SUDO" ] && [ "$DRY" -eq 0 ] && ! have sudo; then
            die "there is no sudo here to install them with. As root: $INSTALL"
        fi
        if [ "$MANAGER" = "apt" ] && [ "$LISTS_FETCHED" -eq 0 ]; then
            # A stale package list is the usual reason an install cannot find its files.
            # shellcheck disable=SC2086
            run ${SUDO}apt-get update
        fi
        # $SUDO and $INSTALL are unquoted on purpose: each is a command and its arguments.
        # shellcheck disable=SC2086
        if ! run $SUDO$INSTALL; then
            case "$MANAGER" in
                pacman) die "pacman did not install them. When it cannot find or fetch a package the system's lists are behind: bring it up to date (sudo pacman -Syu) and run this again." ;;
                *) die "the package manager did not install them: what it said is above. Once that is dealt with, run this again." ;;
            esac
        fi
        INSTALLED=1
        # The shell remembers where it last found a command: look again.
        hash -r 2>/dev/null || true
    fi
fi

# Checked rather than assumed: a package can install and still not be what was needed.
if [ "$DRY" -eq 0 ] || [ "$INSTALLED" -eq 0 ]; then
    if [ "$NEED_JAVA" -eq 1 ] && [ "$DRY" -eq 0 ]; then
        STARTER=$(starter_java || true)
        if [ -z "$STARTER" ] || [ -z "$(java_major "$STARTER" || true)" ]; then
            hint=""
            for studio in $(android_studios | tr ' ' '?'); do
                if [ -x "$studio/jbr/bin/java" ]; then hint=" Android Studio's own will do: export JAVA_HOME=\"$studio/jbr\"."; break; fi
            done
            die "there is no Java to start the build with. Install a JDK, 21 for preference (https://adoptium.net), and run this again.$hint"
        fi
    fi

    if [ "$NEED_PYTHON" -eq 1 ] && [ "$DRY" -eq 0 ]; then
        PYTHON=$(find_python || true)
        if [ -z "$PYTHON" ]; then
            die "the backend needs Python 3.11 or newer, and this system has none that this script can install. Install one (python.org, pyenv, uv) and run this again, or pass --no-backend to set up the rest."
        fi
        python_has_venv "$PYTHON" \
            || die "$("$PYTHON" --version 2>&1) has no venv module, which the backend's install needs. Install it (on Debian and Ubuntu: the python3-venv package) and run this again."
    fi

    if [ "$NEED_BINUTILS" -eq 1 ] && [ "$DRY" -eq 0 ] && ! have objdump; then
        die "freezing the backend into the package needs objdump, of the binutils package. Install it and run this again, or pass --no-backend to build a package without a backend."
    fi

    if [ -z "$NODE_FROM" ] && [ "$FETCH_NODE" -eq 0 ] && [ "$DRY" -eq 0 ]; then
        if have node && have npm && node_usable "$(command -v node)" "$(command -v npm)"; then
            NODE_FROM=system
        else
            # The distribution's turned out too old, or was not installed.
            FETCH_NODE=1
        fi
    fi
fi

if [ "$FETCH_NODE" -eq 1 ]; then
    step "Node.js"
    fetch_node
fi

# --- the backend --------------------------------------------------------------

if [ "$BACKEND" -eq 1 ] && [ ! -x "$REPO/.venv/bin/code-ai" ]; then
    step "The backend"
    say "Installing it into $REPO/.venv (the repository's install.sh)"
    run sh "$REPO/install.sh" || die "the backend did not install: what pip said is above. Run this again once that is dealt with, or pass --no-backend to set up the rest."
fi

# --- the IDE to build against -------------------------------------------------

step "Android Studio"
SINCE=$(sed -n 's/^sinceBuild *= *//p' "$HERE/gradle.properties" | head -n 1)
CHOSEN=""
if [ -n "$IDE" ]; then
    [ -d "$IDE" ] || die "--ide: no such folder: $IDE"
    IDE=$(cd "$IDE" && pwd)
    product_info "$IDE" >/dev/null 2>&1 || die "--ide: $IDE is not an IDE (there is no product-info.json in it)"
    CHOSEN=$IDE
else
    CHOSEN=$(local_setting ideDir || true)
    if [ -n "$CHOSEN" ] && ! product_info "$CHOSEN" >/dev/null 2>&1; then
        warn "local.properties names $CHOSEN, which is not there any more"
        CHOSEN=""
    fi
    if [ -z "$CHOSEN" ]; then
        # The first that is new enough for the plugin; an older one is only named.
        OLDER=""
        while IFS= read -r studio; do
            [ -n "$studio" ] || continue
            branch=$(ide_branch "$studio" || true)
            if [ -z "$SINCE" ] || [ -z "$branch" ] || [ "$branch" -ge "$SINCE" ]; then
                CHOSEN=$studio
                break
            fi
            [ -n "$OLDER" ] || OLDER="$studio (platform $branch)"
        done <<FOUND
$(android_studios)
FOUND
        if [ -z "$CHOSEN" ] && [ -n "$OLDER" ]; then
            say "Found $OLDER, and the plugin needs platform $SINCE or newer (Android Studio 2025.3): not building against it."
        fi
    fi
fi

if [ -n "$CHOSEN" ]; then
    BRANCH=$(ide_branch "$CHOSEN" || true)
    say "Building against: $CHOSEN${BRANCH:+ (platform $BRANCH)}"
    if [ "$(local_setting ideDir || true)" != "$CHOSEN" ]; then
        set_local ideDir "$CHOSEN"
    fi
    if [ -n "$BRANCH" ] && [ -n "$SINCE" ] && [ "$BRANCH" -lt "$SINCE" ]; then
        warn "this IDE is platform $BRANCH and the plugin asks for $SINCE or newer: it will not load the plugin. Update Android Studio, or take ideDir out of local.properties to build against the release named in gradle.properties."
    fi
else
    say "The build will download the release named in gradle.properties (over a gigabyte), and the sandbox runs that one."
    say "To use one that is installed: ./setup.sh --ide /path/to/android-studio"
fi

# --- the plugin ---------------------------------------------------------------

if [ "$BUILD" -eq 1 ]; then
    step "Building the package"
    say "The first build fetches Gradle, the build's own libraries and the panel's npm packages."
    cd "$HERE"
    if [ "$BACKEND" -eq 1 ]; then
        say "It also freezes the backend into the package, which takes a few minutes the first time."
        run ./gradlew buildPlugin || die "the build failed: its first error is above. Running ./setup.sh again goes on from where it stopped."
    else
        run ./gradlew buildPlugin -Ptarget=none || die "the build failed: its first error is above. Running ./setup.sh again goes on from where it stopped."
    fi
fi

# --- what next ----------------------------------------------------------------

step "Ready"
PACKAGE=""
if [ "$BUILD" -eq 1 ] && [ "$DRY" -eq 0 ]; then
    # The newest: an older one may lie beside it.
    PACKAGE=$(ls -t "$HERE"/build/distributions/*.zip 2>/dev/null | head -n 1 || true)
fi
say "Try it:      cd $HERE && ./gradlew runIde"
say "             A separate Android Studio opens with the plugin in it: Code-AI is on the right."
say "             The first run fetches the embedded browser the panel is drawn in (about 150 MB)."
say "             With a project: ./gradlew runIde -PideArgs=\"/path/to/a/project\""
say "Install it:  Settings | Plugins | gear | Install Plugin from Disk, in any Android Studio:"
say "             ${PACKAGE:-build/distributions/code-ai-intellij-<version>-<system>.zip, once it is built}"
if [ "$BACKEND" -eq 1 ]; then
    say "             It has the backend inside: nothing else has to be on that machine."
else
    say "             It has no backend inside: under Settings | Tools | Code-AI, Command is the code-ai to run."
fi
say "             The Code-AI tool window asks for the embedded browser the first time, and gets it itself."

if [ "$SYSTEM" = "Linux" ] && [ "$DRY" -eq 0 ]; then
    STILL_MISSING=$(missing_libraries | cut -d '|' -f 1 | tr '\n' ' ')
    if [ -n "$STILL_MISSING" ]; then
        printf '\n'
        warn "the sandbox IDE and its embedded browser load system libraries that are not here:
    $STILL_MISSING
The plugin builds without them; the IDE or the Code-AI panel may not open. They are the ones any
Chromium-based browser needs: installing Chromium or Chrome from your distribution brings them."
    fi
fi
