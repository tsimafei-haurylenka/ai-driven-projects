#!/usr/bin/env bash
#
# install.sh — install and configure the llama-metrics SERVER.
#
# The server is a Python metrics service (HTTP + MCP) that runs on the
# GPU/inference box next to llama-swap. It exposes /perf /gpu /sys /stats on
# :8090 and an MCP endpoint on :8091. It is independent of the pi client —
# see the sibling ../pi folder for the client extension and skill.
#
# Usage:
#   ./install.sh [command] [options]
#
# Commands:
#   install     Install the server (default)
#   check       Probe the server endpoints
#   uninstall   Remove the server
#
# Options:
#   --host HOST       Host to probe for check (default: localhost)
#   --port PORT       Server port (default: 8090)
#   --prefix DIR      Server install dir (default: ~/llama-metrics)
#   --system          Install a *system* systemd unit (needs sudo)
#   --no-systemd      Do not touch systemd; print manual run instructions
#   --dry-run         Show what would be done without doing it
#   -h, --help        Show this help
#
# Examples:
#   ./install.sh install
#   ./install.sh check
#   sudo ./install.sh install --system
#
set -euo pipefail

# When run under sudo, SUDO_USER is the real user; use their home so the
# install lands in the right place and the unit runs as the right user.
if [[ -n "${SUDO_USER:-}" ]]; then
  export HOME="/home/$SUDO_USER"
  REAL_USER="$SUDO_USER"
else
  REAL_USER="$(id -un)"
fi

# ---- locate repo root (dir containing this script) ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$SCRIPT_DIR/server"

# ---- defaults ----
CMD="install"
HOST="localhost"
PORT="8090"
PREFIX="$HOME/llama-metrics"
USE_SYSTEM=0
NO_SYSTEMD=0
DRY_RUN=0

# ---- helpers ----
log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ok\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  warn\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m  err\033[0m %s\n' "$*" >&2; exit 1; }

run() {  # run a command, honoring --dry-run
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '  dry: %s\n' "$*"
  else
    "$@"
  fi
}

have() { command -v "$1" >/dev/null 2>&1; }

usage() { sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'; }

# ---- arg parsing ----
while [[ $# -gt 0 ]]; do
  case "$1" in
    install|check|uninstall) CMD="$1"; shift ;;
    --host)      HOST="$2"; shift 2 ;;
    --port)      PORT="$2"; shift 2 ;;
    --prefix)    PREFIX="$2"; shift 2 ;;
    --system)    USE_SYSTEM=1; shift ;;
    --no-systemd) NO_SYSTEMD=1; shift ;;
    --dry-run)   DRY_RUN=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

# Expand ~ in user-supplied paths.
PREFIX="${PREFIX/#\$HOME/$HOME}"

# =====================================================================
# INSTALL
# =====================================================================
install() {
  log "Installing llama-metrics server"

  # 1. python
  have python3 || die "python3 not found (install Python 3.10+)"
  local pyver
  pyver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  [[ "$pyver" == 3.1* || "$pyver" == 3.[2-9]* || "$pyver" == 4.* ]] \
    || warn "python $pyver is older than 3.10; the server may not run"

  # 2. files
  log "Installing files to $PREFIX"
  run mkdir -p "$PREFIX"
  run cp "$SERVER_DIR/llama_metrics.py" "$PREFIX/llama_metrics.py"
  if [[ ! -f "$PREFIX/config.json" ]]; then
    run cp "$SERVER_DIR/config.json" "$PREFIX/config.json"
  fi
  ok "server files in place"

  # 3. venv + dependencies (isolated; avoids PEP 668 / system-python pollution).
  # Recreated only if missing; deps reinstalled on every run so updates apply.
  local venv_python="$PREFIX/.venv/bin/python"
  if [[ ! -x "$venv_python" ]]; then
    log "Creating venv at $PREFIX/.venv ..."
    run python3 -m venv "$PREFIX/.venv"
  fi
  log "Installing python deps (mcp, uvicorn) into venv ..."
  run "$venv_python" -m pip install --quiet --disable-pip-version-check \
      -r "$SERVER_DIR/requirements.txt"
  ok "python deps in venv"

  # 4. service
  if [[ "$NO_SYSTEMD" -eq 1 ]]; then
    log "Skipping systemd (--no-systemd). Run manually with:"
    printf '    %s llama_metrics.py\n' "$PREFIX/.venv/bin/python"
    return
  fi

  local unit_dir unit_path
  if [[ "$USE_SYSTEM" -eq 1 ]]; then
    unit_dir="/etc/systemd/system"
    unit_path="$unit_dir/llama-metrics.service"
    [[ "$(id -u)" -eq 0 ]] || die "--system needs root (run with sudo)"
  else
    unit_dir="$HOME/.config/systemd/user"
    unit_path="$unit_dir/llama-metrics.service"
  fi

  log "Installing systemd unit -> $unit_path"
  run mkdir -p "$unit_dir"
  local user python
  user="$REAL_USER"
  python="$PREFIX/.venv/bin/python"
  # Substitute placeholders in the unit template.
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '  dry: write unit file\n'
  else
    sed -e "s|__USER__|$user|" \
        -e "s|__PREFIX__|$PREFIX|" \
        -e "s|__PYTHON__|$python|" \
        "$SERVER_DIR/llama-metrics.service" > "$unit_path"
    # User services already run as the owning user; an explicit User= is
    # redundant and trips WSL2's supplementary-groups handling (EPERM).
    # User services also want default.target, not multi-user.target.
    if [[ "$USE_SYSTEM" -eq 0 ]]; then
      sed -i '/^User=/d; s|WantedBy=multi-user.target|WantedBy=default.target|' "$unit_path"
    fi
  fi

  local sd
  if [[ "$USE_SYSTEM" -eq 1 ]]; then sd="systemctl"; else sd="systemctl --user"; fi
  run $sd daemon-reload
  run $sd enable llama-metrics.service
  run $sd restart llama-metrics.service
  ok "service (re)started"
}

# =====================================================================
# CHECK
# =====================================================================
check() {
  log "Checking llama-metrics server on $HOST:$PORT"
  local fail=0
  for ep in health perf gpu sys stats; do
    local code
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 "http://$HOST:$PORT/$ep" || echo 000)"
    if [[ "$code" == "200" ]]; then
      ok "/$ep -> 200"
    else
      warn "/$ep -> $code"
      fail=1
    fi
  done
  [[ "$fail" -eq 0 ]] && ok "check passed" || warn "check found issues"
}

# =====================================================================
# UNINSTALL
# =====================================================================
uninstall() {
  log "Uninstalling llama-metrics server"
  if [[ "$NO_SYSTEMD" -eq 0 ]]; then
    local sd
    if [[ "$USE_SYSTEM" -eq 1 ]]; then sd="systemctl"; else sd="systemctl --user"; fi
    run $sd disable --now llama-metrics.service || true
    run rm -f "$HOME/.config/systemd/user/llama-metrics.service"
    run rm -f "/etc/systemd/system/llama-metrics.service"
    run $sd daemon-reload || true
  fi
  run rm -f "$PREFIX/llama_metrics.py"
  ok "uninstall complete"
}

# =====================================================================
# DISPATCH
# =====================================================================
case "$CMD" in
  install)   install ;;
  check)     check ;;
  uninstall) uninstall ;;
esac
