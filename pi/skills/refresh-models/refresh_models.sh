#!/usr/bin/env bash
# Refresh pi/qwen/opencode model lists + contexts (ctx-size/parallel) from the llama-swap server config.
# Targets: WSL (/home/timoti), Windows (/mnt/c/Users/Admin), server (ryzen5800 / 192.168.1.104).
set -euo pipefail
SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
SSH=(sshpass -p enter1 ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8)
# Prefer hostname, fall back to LAN IP; probe = non-interactive auth succeeds
for h in ryzen5800 192.168.1.104; do
  if timeout 15 "${SSH[@]}" "timoti@$h" true 2>/dev/null; then SERVER="timoti@$h"; break; fi
done
[ -n "${SERVER:-}" ] || { echo "server unreachable (tried: ryzen5800, 192.168.1.104)" >&2; exit 1; }
echo "using server $SERVER"
SWAP_CFG="/home/timoti/llama-swap/config.yml"
DEFAULT="${1:-qwen3.8-27b-IQ3XXS-Uncensored}"

SWAP="$(mktemp)"
EXTRA=()
trap 'rm -f "$SWAP" ${EXTRA[@]:-}' EXIT
"${SSH[@]}" "$SERVER" "cat $SWAP_CFG" > "$SWAP"

# engine files referenced by --config (e.g. Strata) live on the server; fetch them for the local runs
CMAP=()
while IFS= read -r f; do
  [ -n "$f" ] || continue
  loc="$(mktemp)"
  if "${SSH[@]}" "$SERVER" "cat $f" > "$loc" 2>/dev/null < /dev/null; then
    CMAP+=(--config-map "$f=$loc")
    EXTRA+=("$loc")
  else
    rm -f "$loc"
  fi
done < <("${SSH[@]}" "$SERVER" "grep -oE -- '--config [^ ]+' $SWAP_CFG | awk '{print \$2}'")

python3 "$SKILL_DIR/refresh_models.py" --root /home/timoti --swap "$SWAP" --default "$DEFAULT" "${CMAP[@]}" < /dev/null
python3 "$SKILL_DIR/refresh_models.py" --root /mnt/c/Users/Admin --swap "$SWAP" --default "$DEFAULT" "${CMAP[@]}"
"${SSH[@]}" "$SERVER" "mkdir -p /tmp/refresh-models"
sshpass -p enter1 scp -o StrictHostKeyChecking=no -o ConnectTimeout=8 "$SKILL_DIR/refresh_models.py" "$SERVER:/tmp/refresh-models/refresh_models.py"
"${SSH[@]}" "$SERVER" "python3 /tmp/refresh-models/refresh_models.py --root /home/timoti --swap $SWAP_CFG --default '$DEFAULT'"
