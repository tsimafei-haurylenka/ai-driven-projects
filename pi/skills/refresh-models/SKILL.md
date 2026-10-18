---
name: refresh-models
description: Refresh the local model lists and context windows of the pi, qwen, and opencode harness configs (WSL, Windows, and the ryzen5800 / 192.168.1.104 server) from the llama-swap config.yml on the server. Effective context = ctx-size / parallel. Use when the user asks to refresh/sync model configs, update context windows, or /refresh_models.
---

# Refresh models

One command does everything (fetches the server config, syncs all 12 config files, verifies):

```bash
bash <skill_dir>/refresh_models.sh "$PI_MODEL"
```

- Always pass the currently selected model explicitly — `$PI_MODEL` holds it inside a pi session. The argument becomes `defaultModel`/model name in all 12 configs; passing anything other than the current model makes active sessions reload their model and re-sync context, which must be avoided.
- Only omit the argument (falls back to hardcoded `qwen3.8-27b-IQ3XXS-Uncensored`) when running outside a pi session where no "current" model exists.
- Targets: WSL `/home/timoti`, Windows `/mnt/c/Users/Admin`, server (`ryzen5800` tried first, then LAN IP `192.168.1.104`; a host counts as reachable only when non-interactive SSH auth succeeds, so Tailscale's interactive browser-auth path falls through to the next candidate) — each with `.pi/agent/models.json`, `.pi/agent/settings.json`, `.qwen/settings.json`, `.config/opencode/opencode.json`. Fails fast if both are unreachable.
- Harness model lists are replaced by the llama-swap model list; contexts are `ctx-size // parallel` from each model's `cmd`. Models not in llama-swap (e.g. gemma) are dropped.
- `*-remote` providers (Tailscale IP `100.92.217.80`, for use away from the LAN) are mirrored from their LAN base provider automatically. The script rewrites model lists only — never `baseUrl` or API keys.
- Backups: `<file>.bak-refresh` next to each file.
- Output: `OK`/`FAIL` per file, then the model table. Exit 0 only if all checks pass.

Single-machine variant: `python3 <skill_dir>/refresh_models.py --root <home> --swap <path-to-config.yml> --default "$PI_MODEL"`. (Same rule: pass the current model, never rely on the hardcoded fallback.)
