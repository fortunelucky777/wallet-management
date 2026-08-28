#!/usr/bin/env bash
# walletcli one-command installer.
#   ./install.sh            install (creates an isolated venv, adds `wallet` to PATH)
#   ./install.sh --uninstall
set -euo pipefail

BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
say()  { printf '%s\n' "${CYAN}›${RESET} $*"; }
ok()   { printf '%s\n' "${GREEN}✔${RESET} $*"; }
warn() { printf '%s\n' "${YELLOW}⚠${RESET} $*"; }
die()  { printf '%s\n' "${RED}✘${RESET} $*" >&2; exit 1; }

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_HOME="${WALLETCLI_HOME:-$HOME/.walletcli}"
VENV="$APP_HOME/venv"
BIN_DIR="$HOME/.local/bin"
LINK="$BIN_DIR/wallet"

if [[ "${1:-}" == "--uninstall" ]]; then
    rm -f "$LINK"
    rm -rf "$VENV"
    ok "walletcli uninstalled (your vault in $APP_HOME was NOT touched)."
    exit 0
fi

printf '%s\n' "${BOLD}${CYAN}walletcli installer${RESET}"
printf '%s\n' "-------------------"

# 1. Find a suitable Python (3.10+).
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
            PYTHON="$candidate"
            break
        fi
    fi
done
[[ -n "$PYTHON" ]] || die "Python 3.10+ is required. Install it (e.g. 'sudo apt install python3') and re-run."

# Resolve symlinks to the real interpreter. A venv records this path as its
# 'home'; behind a symlink chain (e.g. ~/.local/bin -> a uv-managed CPython)
# the venv's python cannot find its stdlib and dies with
# "No module named 'encodings'".
PYTHON="$("$PYTHON" -c 'import os, sys; print(os.path.realpath(sys.executable))')"
ok "Using $("$PYTHON" --version) ($PYTHON)"

# 2. Verify the venv module works (Debian/Ubuntu ship it separately) and that
#    ensurepip can actually seed pip (present even when 'venv --help' passes).
"$PYTHON" -m venv --help >/dev/null 2>&1 \
    || die "The 'venv' module is missing. On Debian/Ubuntu: sudo apt install python3-venv"
"$PYTHON" -m ensurepip --version >/dev/null 2>&1 \
    || die "Python's 'ensurepip' is unavailable, so venvs can't be created. On Debian/Ubuntu: sudo apt install python3-venv (or python3.X-venv matching your Python)."

# 3. Create an isolated environment and install.
if [[ -x "$VENV/bin/python" ]] && ! "$VENV/bin/python" -c 'import encodings' >/dev/null 2>&1; then
    warn "Existing venv in $VENV is broken — recreating it."
    rm -rf "$VENV"
fi
say "Creating isolated environment in $VENV …"
mkdir -p "$APP_HOME" && chmod 700 "$APP_HOME"
"$PYTHON" -m venv "$VENV"
say "Installing walletcli and dependencies (takes a minute on first run) …"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet "$REPO_DIR"
ok "Installed $("$VENV/bin/wallet" --version 2>/dev/null || echo walletcli)"

# 4. Put `wallet` on the PATH.
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/wallet" "$LINK"
ok "Linked $LINK"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *)
        SHELL_RC="$HOME/.bashrc"
        [[ "${SHELL:-}" == */zsh ]] && SHELL_RC="$HOME/.zshrc"
        if ! grep -qs '\.local/bin' "$SHELL_RC" 2>/dev/null; then
            printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$SHELL_RC"
            warn "Added ~/.local/bin to PATH in $SHELL_RC — open a new terminal (or 'source $SHELL_RC')."
        else
            warn "~/.local/bin is not on PATH in this shell — open a new terminal."
        fi
        ;;
esac

printf '\n%s\n' "${GREEN}${BOLD}All set!${RESET} Try:"
printf '%s\n'   "    ${BOLD}wallet${RESET}            overview of every command"
printf '%s\n'   "    ${BOLD}wallet register${RESET}   create your first wallet"
printf '%s\n'   "    ${BOLD}wallet doctor${RESET}     check connectivity"
