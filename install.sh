#!/bin/sh
# Production bootstrap launcher — Linux/macOS (POSIX sh; plan §5).
#
# ONE job: guarantee a Python >= 3.10 exists, then hand off to the brain
# (install.py, stdlib-only) which does the real audit/install/verify.
#
#   1. use an existing python3 >= 3.10 if present
#   2. else, with sudo+pkg-mgr: install via the system package manager
#   3. else (no sudo / no pkg-mgr): download a relocatable
#      python-build-standalone CPython into ./.python  (fully autonomous,
#      no root, arch + libc aware, SHA-256 verified)
#
# Then: exec "$PY" install.py "$@"   (all args are forwarded verbatim)
#
#   ./install.sh                 full install (precise alignment incl.)
#   ./install.sh --check         audit + verify only, no changes
#   ./install.sh --profile minimal   skip the big torch download
set -eu

ROOT="$(cd "$(dirname "$0")" && pwd)"
MIN="(3, 10)"
# Pinned python-build-standalone release (no-sudo fallback). Bump together.
PBS_TAG="20240814"
PBS_PY="3.12.5"
PBS_BASE="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}"

# colour only on a real TTY and when NO_COLOR is unset (clean CI logs)
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then _C=1; else _C=; fi
c_ok()   { [ -n "$_C" ] && printf '\033[32m%s\033[0m\n' "$*" || echo "$*"; }
c_warn() { [ -n "$_C" ] && printf '\033[33m%s\033[0m\n' "$*" || echo "$*"; }
c_err()  { [ -n "$_C" ] && printf '\033[31m%s\033[0m\n' "$*" || echo "$*"; }
say()    { echo "$*"; }

# --- 1. is there already a usable python? --------------------------------
py_ok() {  # $1 = candidate; true if it runs and is >= 3.10
    [ -n "${1:-}" ] || return 1
    command -v "$1" >/dev/null 2>&1 || [ -x "$1" ] || return 1
    "$1" -c "import sys;raise SystemExit(0 if sys.version_info[:2]>=${MIN} else 1)" \
        >/dev/null 2>&1
}

find_python() {
    for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
        if py_ok "$c"; then command -v "$c"; return 0; fi
    done
    [ -x "$ROOT/.python/python/bin/python3" ] && \
        py_ok "$ROOT/.python/python/bin/python3" && {
            echo "$ROOT/.python/python/bin/python3"; return 0; }
    return 1
}

# --- 2. system package manager (needs root or sudo) ----------------------
SUDO=""
if [ "$(id -u 2>/dev/null || echo 1)" = "0" ]; then SUDO=""
elif command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

pkg_install_python() {
    [ -n "${SUDO}" ] || [ "$(id -u 2>/dev/null || echo 1)" = "0" ] || return 1
    if command -v apt-get >/dev/null 2>&1; then
        $SUDO apt-get update -y && \
        $SUDO apt-get install -y python3 python3-venv python3-pip python3-tk
    elif command -v dnf >/dev/null 2>&1; then
        $SUDO dnf install -y python3 python3-pip python3-tkinter
    elif command -v yum >/dev/null 2>&1; then
        $SUDO yum install -y python3 python3-pip
    elif command -v pacman >/dev/null 2>&1; then
        $SUDO pacman -S --noconfirm python python-pip tk
    elif command -v zypper >/dev/null 2>&1; then
        $SUDO zypper -n install python3 python3-pip python3-tk
    elif command -v apk >/dev/null 2>&1; then
        $SUDO apk add python3 py3-pip
    elif command -v brew >/dev/null 2>&1; then
        brew install python-tk
    else
        return 1
    fi
}

# --- 3. userland python-build-standalone (no sudo, autonomous) -----------
fetch() {  # $1 url  $2 dest  (curl or wget, with a visible progress bar)
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 3 --connect-timeout 20 -o "$2" "$1"
    elif command -v wget >/dev/null 2>&1; then
        wget -t 3 -T 20 -O "$2" "$1"
    else
        c_err "Need curl or wget to download Python. Install one and re-run."
        return 1
    fi
}

pbs_asset() {  # echo the install_only asset name for this machine
    arch="$(uname -m)"
    case "$arch" in
        x86_64|amd64) a="x86_64" ;;
        aarch64|arm64) a="aarch64" ;;
        *) c_err "unsupported arch: $arch"; return 1 ;;
    esac
    if [ "$(uname -s)" = "Darwin" ]; then
        echo "cpython-${PBS_PY}+${PBS_TAG}-${a}-apple-darwin-install_only.tar.gz"
        return 0
    fi
    libc="gnu"
    if command -v ldd >/dev/null 2>&1 && ldd --version 2>&1 | \
        grep -qi musl; then libc="musl"; fi
    echo "cpython-${PBS_PY}+${PBS_TAG}-${a}-unknown-linux-${libc}-install_only.tar.gz"
}

install_pbs() {
    asset="$(pbs_asset)" || return 1
    url="${PBS_BASE}/${asset}"
    tgz="$ROOT/.python.tar.gz"
    say ""
    c_warn "No suitable Python and no package manager/sudo available."
    say "Downloading a self-contained Python (~30 MB), no admin needed:"
    say "  $url"
    fetch "$url" "$tgz" || return 1

    # integrity: verify against the release's published SHA256SUMS (TLS)
    sums="$ROOT/.python.SHA256SUMS"
    if fetch "${PBS_BASE}/SHA256SUMS" "$sums" 2>/dev/null; then
        want="$(grep " ${asset}\$" "$sums" 2>/dev/null | awk '{print $1}')"
        if [ -n "${want:-}" ] && command -v sha256sum >/dev/null 2>&1; then
            got="$(sha256sum "$tgz" | awk '{print $1}')"
            if [ "$want" != "$got" ]; then
                c_err "SHA-256 mismatch for $asset — aborting."
                rm -f "$tgz" "$sums"; return 1
            fi
            c_ok "  checksum verified"
        else
            c_warn "  (could not verify checksum; proceeding over HTTPS)"
        fi
        rm -f "$sums"
    fi

    rm -rf "$ROOT/.python"
    mkdir -p "$ROOT/.python"
    tar -xzf "$tgz" -C "$ROOT/.python" || { c_err "extract failed"; return 1; }
    rm -f "$tgz"
    [ -x "$ROOT/.python/python/bin/python3" ]
}

# --- orchestrate ---------------------------------------------------------
say "Video Production — bootstrap (Linux/macOS)"

if PY="$(find_python 2>/dev/null)"; then
    c_ok "Found Python: $PY"
else
    say "No Python >= 3.10 found — installing one…"
    if pkg_install_python && PY="$(find_python 2>/dev/null)"; then
        c_ok "Installed system Python: $PY"
    elif install_pbs && PY="$ROOT/.python/python/bin/python3"; then
        c_ok "Installed userland Python: $PY"
    else
        c_err "Could not obtain Python automatically."
        say  "Install Python >= 3.10 manually, then re-run ./install.sh"
        exit 4
    fi
fi

exec "$PY" "$ROOT/install.py" "$@"
