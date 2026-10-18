#!/usr/bin/env bash
# Install a launcher named `pb` for perf-bench. No python dependencies required.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${1:-$HOME/.local/bin}"
if [ "${1:-}" = "--uninstall" ]; then rm -f "${PREFIX}/pb" "${PREFIX}/pb-summarize"; echo "removed from ${PREFIX}"; exit 0; fi
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
mkdir -p "$PREFIX"
cat > "${PREFIX}/pb" << EOF2
#!/usr/bin/env bash
exec python3 "${HERE}/bench.py" "\$@"
EOF2
cat > "${PREFIX}/pb-summarize" << EOF2
#!/usr/bin/env bash
exec python3 "${HERE}/summarize.py" "\$@"
EOF2
chmod +x "${PREFIX}/pb" "${PREFIX}/pb-summarize"
echo "installed: ${PREFIX}/pb  ->  ${HERE}/bench.py"
case ":$(echo "$PATH" | tr ':' ':'):" in *":${PREFIX}:"*) ;; *)
  echo "note: add ${PREFIX} to PATH (e.g. export PATH=\"${PREFIX}:\$PATH\")";; esac
cat > "${HERE}/perf-bench.json" 2>/dev/null << 'EOF2' || true
{
 "targets": ["local"],
 "model": "PUT-YOUR-MODEL-ID-HERE",
 "tests": ["health", "prefill", "decode", "kv-cache"],
 "tag": "default"
}
EOF2
[ -f "${HERE}/presets/targets.json" ] || cp "${HERE}/presets/targets.example.json" "${HERE}/presets/targets.json"
echo "config: ${HERE}/perf-bench.json (edit targets/model); presets: ${HERE}/presets/targets.json"
