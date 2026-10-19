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
#   install     Install/update the server on the local machine (default)
#   deploy      Push the repo to a remote server and run install there
#   check       Probe the server endpoints
#   uninstall   Remove the server
#
# Options:
#   --host HOST       deploy target / check host (deploy: required)
#   --user USER       deploy ssh user (default: current user)
#   --port PORT       Server port (default: 8090)
#   --prefix DIR      Server install dir (default: ~/llama-metrics)
#   --system          Install a *system* systemd unit (needs sudo)
#   --no-systemd      Do not touch systemd; print manual run instructions
#   --dry-run         Show what would be done without doing it
#   -h, --help        Show this help
#
# Deploy reads credentials from env (never stored in the repo):
#   SSH_PASS   ssh password (uses sshpass when set)
#   SUDO_PASS  remote sudo password (piped to sudo -S; else an interactive
#              prompt is shown via ssh -t)
#
# Examples:
#   ./install.sh install
#   ./install.sh check
#   sudo ./install.sh install --system
#   SSH_PASS=... SUDO_PASS=... ./install.sh deploy --host 192.168.1.104 --user timoti
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
DEPLOY_USER="$(id -un)"
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

usage() { sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; }

# ---- arg parsing ----
while [[ $# -gt 0 ]]; do
  case "$1" in
    install|deploy|check|uninstall) CMD="$1"; shift ;;
    --host)      HOST="$2"; shift 2 ;;
    --user)      DEPLOY_USER="$2"; shift 2 ;;
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

  # 2. files (keep a backup of the previous main file when it differs)
  log "Installing files to $PREFIX"
  run mkdir -p "$PREFIX"
  if [[ -f "$PREFIX/llama_metrics.py" ]] && ! cmp -s "$SERVER_DIR/llama_metrics.py" "$PREFIX/llama_metrics.py"; then
    run cp "$PREFIX/llama_metrics.py" "$PREFIX/llama_metrics.py.bak.$(date +%Y%m%d-%H%M%S)"
  fi
  run cp "$SERVER_DIR/llama_metrics.py" "$PREFIX/llama_metrics.py"
  if [[ ! -f "$PREFIX/config.json" ]]; then
    run cp "$SERVER_DIR/config.json" "$PREFIX/config.json"
  fi
  # under sudo, installed files land root-owned; the service runs as the real user
  if [[ "$(id -u)" -eq 0 && -n "${SUDO_USER:-}" ]]; then
    run chown -R "$REAL_USER":"$REAL_USER" "$PREFIX"
  fi
  ok "server files in place"

  # 3. venv + dependencies (isolated; avoids PEP 668 / system-python pollution).
  # Recreated only if missing; deps reinstalled on every run so updates apply.
  local venv_python="$PREFIX/.venv/bin/python"
  if [[ "$DRY_RUN" -eq 0 && ! -x "$venv_python" ]]; then
    log "Creating venv at $PREFIX/.venv ..."
    python3 -m venv "$PREFIX/.venv" || warn "venv creation failed; will try system python"
  fi
  log "Installing python deps (mcp, uvicorn) into venv ..."
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '  dry: pip install -r requirements.txt\n'
  elif [[ -x "$venv_python" ]] && "$venv_python" -m pip install --quiet --disable-pip-version-check -r "$SERVER_DIR/requirements.txt"; then
    ok "python deps in venv"
  elif python3 -c 'import mcp.server.mcpserver' 2>/dev/null; then
    warn "venv/pip failed, but system python already has deps -> using $(command -v python3)"
    VENV_FAILED=1
  else
    die "cannot provide deps: venv pip failed and system python lacks 'mcp'"
  fi

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
  [[ "${VENV_FAILED:-0}" -eq 1 ]] && python="$(command -v python3)"
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
# DEPLOY (repo -> remote server -> install.sh install there)
# =====================================================================
deploy() {
  [[ "$HOST" != "localhost" ]] || die "deploy needs --host HOST"
  local target="$DEPLOY_USER@$HOST"
  local stage="llama-metrics-stage"
  local stage_abs="/home/$DEPLOY_USER/$stage"
  local ssh_opts=(-o StrictHostKeyChecking=no)
  local ssh_cmd=(ssh "${ssh_opts[@]}") rsync_e=("ssh ${ssh_opts[*]}")
  if [[ -n "${SSH_PASS:-}" ]] && have sshpass; then
    ssh_cmd=(sshpass -p "$SSH_PASS" ssh "${ssh_opts[@]}")
    rsync_e=("sshpass -p $SSH_PASS ssh ${ssh_opts[*]}")
  fi

  log "Staging repo files -> $target:~/$stage"
  rsync -az --delete -e "${rsync_e[0]}" \
    --exclude '__pycache__' --exclude '*.bak*' --exclude 'srv.pid' --exclude '.venv' \
    "$SCRIPT_DIR/install.sh" "$target:$stage/"
  rsync -az --delete -e "${rsync_e[0]}" \
    --exclude '__pycache__' --exclude '*.bak*' --exclude 'srv.pid' \
    "$SERVER_DIR/" "$target:$stage/server/"

  log "Running 'install.sh install --system' on $HOST"
  local remote_cmd="cd $stage_abs && ./install.sh install --system --prefix /home/$DEPLOY_USER/llama-metrics"
  if [[ -n "${SUDO_PASS:-}" ]]; then
    printf '%s\n' "$SUDO_PASS" | "${ssh_cmd[@]}" "$target" "sudo -S bash -c '$remote_cmd'"
  else
    "${ssh_cmd[@]}" -t "$target" "sudo bash -c '$remote_cmd'"
  fi

  log "Waiting for the service to answer on $HOST"
  "${ssh_cmd[@]}" "$target" \
    "for i in \$(seq 1 15); do code=\$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 http://localhost:$PORT/health); [ \"\$code\" = 200 ] && break; sleep 1; done; \
     if [ \"\$code\" != 200 ]; then echo 'service did not come up' >&2; exit 1; fi; \
     for ep in health perf gpu sys stats; do printf '%-6s %s\n' \"\$ep\" \"\$(curl -s -o /dev/null -w '%{http_code}' --max-time 4 http://localhost:$PORT/\$ep)\"; done" || die "deploy failed: service not answering"
  ok "deploy complete"
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
  deploy)    deploy ;;
  check)     check ;;
  uninstall) uninstall ;;
esac
