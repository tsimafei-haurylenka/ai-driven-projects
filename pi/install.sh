#!/usr/bin/env bash
#
# install.sh — install and configure the llama-metrics PI CLIENT.
#
# The client is a pi extension (TypeScript) that runs wherever pi runs. It
# polls the llama-metrics server and paints a live status bar above the
# editor. This folder also ships an optional pi skill (refresh-models). It is
# independent of the server — see the sibling ../llama-metrics folder for the
# server service.
#
# Usage:
#   ./install.sh [command] [options]
#
# Commands:
#   install     Install the client extension (default)
#   skill       Install the refresh-models skill
#   all         Install the extension and the skill
#   check       Verify the client files and config
#   uninstall   Remove the client extension and skill
#
# Options:
#   --host HOST       Server host the client should talk to (default: localhost)
#   --port PORT       Server port (default: 8090)
#   --token TOKEN     Bearer token sent by the client (optional)
#   --pi-dir DIR      pi agent dir (default: ~/.pi/agent)
#   --dry-run         Show what would be done without doing it
#   -h, --help        Show this help
#
# Examples:
#   ./install.sh install
#   ./install.sh all
#   ./install.sh install --host <gpu-box-hostname>
#
set -euo pipefail

# ---- locate repo root (dir containing this script) ----
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLIENT_DIR="$SCRIPT_DIR/client"
SKILL_DIR="$SCRIPT_DIR/skills/refresh-models"

# ---- defaults ----
CMD="install"
HOST="localhost"
PORT="8090"
TOKEN=""
PI_DIR="$HOME/.pi/agent"
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

usage() { sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; }

# ---- arg parsing ----
while [[ $# -gt 0 ]]; do
  case "$1" in
    install|skill|all|check|uninstall) CMD="$1"; shift ;;
    --host)      HOST="$2"; shift 2 ;;
    --port)      PORT="$2"; shift 2 ;;
    --token)     TOKEN="$2"; shift 2 ;;
    --pi-dir)    PI_DIR="$2"; shift 2 ;;
    --dry-run)   DRY_RUN=1; shift ;;
    -h|--help)   usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

# Expand ~ in user-supplied paths.
PI_DIR="${PI_DIR/#\$HOME/$HOME}"

# =====================================================================
# INSTALL (extension)
# =====================================================================
install() {
  log "Installing llama-metrics pi extension"

  have pi || warn "pi not found on PATH; the extension needs pi to be installed"

  local ext_dir="$PI_DIR/extensions"
  log "Installing extension to $ext_dir"
  run mkdir -p "$ext_dir"
  run cp "$CLIENT_DIR/llama-status.ts" "$ext_dir/llama-status.ts"
  ok "extension in place"

  # Merge the llamaStatus block into settings.json (create if missing).
  local settings="$PI_DIR/settings.json"
  log "Writing llamaStatus config -> $settings"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '  dry: merge llamaStatus {hosts:[%s], port:%s}\n' "$HOST" "$PORT"
  else
    merge_settings "$settings" "$HOST" "$PORT" "$TOKEN"
  fi
  ok "settings.json updated"
}

# merge_settings SETTINGS HOST PORT TOKEN
# Idempotently set the "llamaStatus" key, preserving everything else.
merge_settings() {
  local settings="$1" host="$2" port="$3" token="$4"
  if command -v node >/dev/null 2>&1; then
    SETTINGS="$settings" HOST="$host" PORT="$port" TOKEN="$token" node -e '
      const fs = require("fs");
      const p = process.env.SETTINGS;
      let cfg = {};
      try { cfg = JSON.parse(fs.readFileSync(p, "utf8")); } catch {}
      const hosts = [process.env.HOST, "127.0.0.1"].filter((h, i, a) => a.indexOf(h) === i);
      const block = { hosts, port: Number(process.env.PORT) };
      if (process.env.TOKEN) block.token = process.env.TOKEN;
      cfg.llamaStatus = block;
      fs.writeFileSync(p, JSON.stringify(cfg, null, 2) + "\n");
    '
  elif command -v python3 >/dev/null 2>&1; then
    SETTINGS="$settings" HOST="$host" PORT="$port" TOKEN="$token" python3 -c '
      import json, os
      p = os.environ["SETTINGS"]
      cfg = {}
      try:
          cfg = json.load(open(p))
      except Exception:
          pass
      hosts = list(dict.fromkeys([os.environ["HOST"], "127.0.0.1"]))
      block = {"hosts": hosts, "port": int(os.environ["PORT"])}
      if os.environ.get("TOKEN"):
          block["token"] = os.environ["TOKEN"]
      cfg["llamaStatus"] = block
      open(p, "w").write(json.dumps(cfg, indent=2) + "\n")
    '
  else
    die "need node or python3 to edit settings.json"
  fi
}

# =====================================================================
# INSTALL (skill)
# =====================================================================
install_skill() {
  log "Installing refresh-models skill"
  local skill_dest="$PI_DIR/skills/refresh-models"
  run mkdir -p "$skill_dest"
  run cp "$SKILL_DIR/SKILL.md"        "$skill_dest/SKILL.md"
  run cp "$SKILL_DIR/refresh_models.py" "$skill_dest/refresh_models.py"
  run cp "$SKILL_DIR/refresh_models.sh" "$skill_dest/refresh_models.sh"
  run chmod +x "$skill_dest/refresh_models.sh"
  ok "skill in place"
}

# =====================================================================
# CHECK
# =====================================================================
check() {
  log "Checking llama-metrics pi client in $PI_DIR"
  local fail=0

  if [[ -f "$PI_DIR/extensions/llama-status.ts" ]]; then
    ok "extension present"
  else
    warn "extension missing: $PI_DIR/extensions/llama-status.ts"
    fail=1
  fi
  if [[ -f "$PI_DIR/settings.json" ]] && grep -q llamaStatus "$PI_DIR/settings.json"; then
    ok "llamaStatus config present"
  else
    warn "llamaStatus config missing in settings.json"
    fail=1
  fi
  if [[ -f "$PI_DIR/skills/refresh-models/SKILL.md" ]]; then
    ok "refresh-models skill present"
  else
    warn "refresh-models skill missing (optional)"
  fi

  [[ "$fail" -eq 0 ]] && ok "check passed" || warn "check found issues"
}

# =====================================================================
# UNINSTALL
# =====================================================================
uninstall() {
  log "Uninstalling llama-metrics pi client"
  run rm -f "$PI_DIR/extensions/llama-status.ts"
  run rm -rf "$PI_DIR/skills/refresh-models"
  ok "uninstall complete (llamaStatus config in settings.json left in place)"
}

# =====================================================================
# DISPATCH
# =====================================================================
case "$CMD" in
  install)   install ;;
  skill)     install_skill ;;
  all)       install; install_skill ;;
  check)     check ;;
  uninstall) uninstall ;;
esac
