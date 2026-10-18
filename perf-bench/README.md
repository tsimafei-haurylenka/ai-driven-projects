# perf-bench

Portable, dependency-free (stdlib python3, bash/powershell only) performance harness for
any OpenAI-compatible inference server: llama.cpp/llama-swap, vLLM, LM Studio, Ollama's
`/v1`, hosted endpoints. Vendor-agnostic: AMD, NVIDIA, Intel, CPU - the harness only
talks HTTP; local GPU counters are probed best-effort via `nvidia-smi` or `rocm-smi`
when present. No hostnames, IPs, models, or paths are hardcoded in the code.

## Quick start

```bash
./install.sh                       # optional: installs `pb` launcher + starter perf-bench.json
python3 bench.py --target local --model PUT-MODEL --tests health,prefill,decode
python3 summarize.py               # markdown pivot of results/runs.csv
```

Windows: `powershell -File install.ps1` then `pb --config perf-bench.json`.

## Resolution order

CLI flags > config file > localhost defaults.
Config file search: `--config` path, `$PERF_BENCH_CONFIG`, `./perf-bench.json`,
`./perf-bench.yaml` (needs PyYAML), `~/.config/perf-bench/config.json|yaml`,
`%APPDATA%\perf-bench\config.json`. Presets: `presets/targets.json` (example provided).
**If neither args nor a config define anything, the tool prints usage and explains that
it is unclear what to benchmark (exit code 2)** instead of guessing.

## What it measures

| test suite | metric | how |
|---|---|---|
| `tests/health.json` | health_ok | `/health` + `/v1/models` reachability, latency |
| `tests/prefill.json` | prefill_tps | unique prompts at several sizes (no cache reuse) |
| `tests/decode.json` | decode_tps, first_token_s | single streaming request per context depth |
| `tests/kv-cache.json` | cache_hit_tps, speedup_vs_cold | cold fill of a large shared prefix, then N queries hitting it |
| `tests/full.json` | all of the above | combined smoke suite |
| `--llama-bench EXE --gguf M.gguf` | pp/tg from the binary | parses llama.cpp `llama-bench` output into the same CSV |

## Useful flags

`--target host:port` (repeatable, or preset name) · `--host/--port/--scheme/--model` ·
`--api-key-env VAR` (key lives in the environment, never in files) ·
`--tests name[,name]` or a suite json · `--depths 1024,8192,32768` ·
`--pp-sizes 512,2048,8192` · `--repeat N` · `--timeout S` · `--gpu auto|off` ·
`--out DIR` · `--tag LABEL` (groups runs in summaries) · `--dry-run` · `--list-tests` · `--list-targets`

## Results

Each run appends to `results/runs.csv` (columns: ts, tag, run_id, target, model, test,
phase, depth, metric, value, first_token_s, detail, gpu_delta_mb) and writes
`results/run-<id>.json`. Compare runs and models:

```bash
python3 summarize.py --group-by tag,model --last 6
python3 summarize.py --metric prefill_tps --out compare.md
```

## Portability notes

- Runs anywhere python3 runs (WSL, Linux, macOS, Windows `py`/`python`).
- Add new suites by dropping a json into `tests/` (see `tests/README.md`), no code changes.
- Corpus text is synthetic with per-request nonces, so prompt caches cannot flatter results.
- GPU delta uses counters captured before/after each target run; on machines without
  vendor CLIs the field is simply empty.
