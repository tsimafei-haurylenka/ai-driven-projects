# Test suites

A suite is a json file: `{"description": "...", "cases": {"name": {spec}}}`.
Case specs:

- `{"type": "health"}`
- `{"type": "prefill", "sweep_values": [512, 2048]} or "tokens": 2048}` - prompt processing
- `{"type": "decode", "sweep_values": [1024, 8192], "tokens": 128}` - generation at context depth
- `{"type": "kv-cache", "prefix_tokens": 8192, "hits": 3, "tokens": 32}` - prompt-cache speedup

`chars_per_token` (default 4) tunes prompt length. `sweep_values` may be replaced by CLI
`--pp-sizes` / `--depths`. Select with `--tests myfile.json` or `--tests prefill,decode`.
