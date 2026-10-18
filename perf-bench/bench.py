#!/usr/bin/env python3
"""perf-bench: parameterized performance probes for any OpenAI-compatible inference server.

Resolve order: CLI flags > config file ($PERF_BENCH_CONFIG, --config, ./perf-bench.json[.yaml],
~/.config/perf-bench/config.json) > built-in localhost defaults. No hardcoded hosts or models.
"""
import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lib.engine import Probe  # noqa: E402
from lib import report  # noqa: E402
from lib import util  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BUILTIN_TESTS = ["health", "prefill", "decode", "kv-cache", "full"]
USAGE_NOTE = (
    "Nothing to test: no target/model/tests were given and no config file was found.\n"
    "Point --target/--host/--model at a server and pick tests, or create a config file\n"
    "(see config.example.json). Examples:\n"
    "  bench.py --host 127.0.0.1 --port 8080 --model my-model --tests prefill,decode\n"
    "  bench.py --config ./perf-bench.json --tag nightly\n"
    "  bench.py --llama-bench /path/to/llama-bench --gguf model.gguf\n"
)


def find_config(cli_path):
    cands = []
    if cli_path:
        cands.append(cli_path)
    if os.environ.get("PERF_BENCH_CONFIG"):
        cands.append(os.environ["PERF_BENCH_CONFIG"])
    cands += ["perf-bench.json", "perf-bench.yaml", ".perf-bench.json"]
    xdg = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    appdata = os.environ.get("APPDATA")
    if appdata:
        cands.append(os.path.join(appdata, "perf-bench", "config.json"))
    cands.append(os.path.join(xdg, "perf-bench", "config.json"))
    cands.append(os.path.join(xdg, "perf-bench", "config.yaml"))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return None


def build_parser():
    p = argparse.ArgumentParser(
        prog="bench.py", description="Portable inference performance harness (OpenAI-compatible API).",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=USAGE_NOTE)
    p.add_argument("--config", help="config file (.json, or .yaml when PyYAML is available)")
    p.add_argument("--target", action="append", default=[], help="preset name from presets file, or host:port")
    p.add_argument("--host", help="server host (default from config, else 127.0.0.1)")
    p.add_argument("--port", type=int, help="server port (default 8080)")
    p.add_argument("--scheme", help="http or https (default http)")
    p.add_argument("--model", help="model id to test (required unless --dry-run/--list-tests)")
    p.add_argument("--api-key-env", help="name of the env var holding the API key")
    p.add_argument("--tests", help="comma list of: " + ",".join(BUILTIN_TESTS) + " or a test-suite file")
    p.add_argument("--tag", help="label for this run (used in summaries)")
    p.add_argument("--depths", help="override context depths for tg/kv tests, e.g. 1024,8192,32768")
    p.add_argument("--pp-sizes", help="override prefill sizes, e.g. 512,2048,8192")
    p.add_argument("--out", help="results directory (default ./results)")
    p.add_argument("--gpu", choices=["auto", "off"], help="probe local GPU stats around the run")
    p.add_argument("--repeat", type=int, help="repeat count per measurement (default 3)")
    p.add_argument("--timeout", type=int, help="per-request timeout seconds (default 600)")
    p.add_argument("--llama-bench", metavar="EXE", help="run llama.cpp llama-bench binary instead of API probes")
    p.add_argument("--gguf", help="model file for --llama-bench")
    p.add_argument("--bench-args", help="extra args passed verbatim to llama-bench")
    p.add_argument("--list-tests", action="store_true", help="list built-in tests and exit")
    p.add_argument("--list-targets", action="store_true", help="list presets from the presets file and exit")
    p.add_argument("--dry-run", action="store_true", help="print the resolved plan and exit")
    return p


def resolve_targets(args, cfg, presets):
    out = []
    items = args.target or cfg.get("targets", [])
    for it in items:
        if isinstance(it, dict):
            out.append(dict(it))
        elif isinstance(it, str) and it in presets:
            out.append(dict(presets[it]))
        elif isinstance(it, str) and ":" in it:
            h, p = it.rsplit(":", 1)
            out.append({"host": h, "port": int(p)})
        elif isinstance(it, str):
            out.append({"host": it})
    if args.host:
        base = {"host": args.host}
        if args.port:
            base["port"] = args.port
        if args.scheme:
            base["scheme"] = args.scheme
        if args.model:
            base["model"] = args.model
        if args.api_key_env:
            base["api_key_env"] = args.api_key_env
        out.append(base)
    if not out:
        c = cfg.get("target")
        if isinstance(c, dict):
            out.append(dict(c))
    defaults = {"host": "127.0.0.1", "port": 8080, "scheme": "http", "model": args.model or cfg.get("model")}
    for t in out:
        for k, v in defaults.items():
            t.setdefault(k, v)
    return out


def load_suite(spec, tests_dir):
    if spec in BUILTIN_TESTS:
        path = os.path.join(tests_dir, spec + ".json")
    else:
        path = spec
    if not os.path.isfile(path):
        util.die(f"test suite not found: {spec} (looked at {path})")
    data = util.read_structured(path)
    return os.path.basename(path).rsplit(".", 1)[0], data


def main():
    parser = build_parser()
    touched_args = [a for a in sys.argv[1:] if not a.startswith("--list") and not a.startswith("--dry")]
    cfg_path = find_config(next((sys.argv[i + 1] for i, a in enumerate(sys.argv)
                                 if a == "--config" and i + 1 < len(sys.argv)), None))
    cfg = util.read_structured(cfg_path) if cfg_path else {}
    args = parser.parse_args()
    presets = util.read_structured(args.presets_file(cfg) if hasattr(args, "presets_file")
                                   else util.first_existing(
                                       os.path.join(HERE, "presets", "targets.json"),
                                       os.path.join(HERE, "presets", "targets.example.json"),
                                       cfg.get("presets", ""))) or {}

    if args.list_targets:
        for k, v in presets.items():
            print(f"{k}: {v.get('host','?')}:{v.get('port','?')} model={v.get('model','-')}")
        return 0
    if args.list_tests:
        for name in BUILTIN_TESTS:
            p = os.path.join(HERE, "tests", name + ".json")
            desc = (util.read_structured(p) or {}).get("description", "") if os.path.isfile(p) else ""
            print(f"{name}: {desc}")
        print(f"(suite files accepted from {os.path.join(HERE, 'tests')})")
        return 0

    if not cfg_path and not touched_args:
        parser.print_help()
        print("\n" + USAGE_NOTE)
        return 2

    tests_spec = args.tests or cfg.get("tests") or "full"
    suites = [load_suite(s.strip(), os.path.join(HERE, "tests"))
              for s in (tests_spec.split(",") if isinstance(tests_spec, str) else tests_spec)]
    targets = resolve_targets(args, cfg, presets)
    if args.dry_run:
        print(json.dumps({"config": cfg_path, "targets": targets,
                          "suites": [s[0] for s in suites], "tag": args.tag or cfg.get("tag", ""),
                          "depths": args.depths or cfg.get("depths"),
                          "pp_sizes": args.pp_sizes or cfg.get("pp_sizes"),
                          "repeat": args.repeat or cfg.get("repeat", 3),
                          "out": args.out or cfg.get("out", "results")}, indent=2))
        return 0
    if not targets:
        parser.print_help()
        print("\n" + USAGE_NOTE)
        return 2

    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    tag = args.tag or cfg.get("tag", "")
    out_dir = args.out or cfg.get("out", os.path.join(HERE, "results"))
    gpu_mode = args.gpu or cfg.get("gpu", "auto")
    repeat = args.repeat or int(cfg.get("repeat", 3))
    timeout = args.timeout or int(cfg.get("timeout", 600))
    depths = [int(x) for x in (args.depths or cfg.get("depths", "1024,8192,32768")).split(",") if x]
    pp_sizes = [int(x) for x in (args.pp_sizes or cfg.get("pp_sizes", "512,2048,8192")).split(",") if x]
    rows = []

    if args.llama_bench:
        from lib.engine import run_llama_bench
        rows += run_llama_bench(args.llama_bench, args.gguf, args.bench_args or cfg.get("bench_args", ""),
                                tag, timeout)
    else:
        for t in targets:
            key = os.environ.get(t.get("api_key_env") or "", "") if t.get("api_key_env") else ""
            pr = Probe(t["scheme"] + "://" + t["host"] + ":" + str(t["port"]), key, timeout)
            util.log(f"target {pr.base} model={t['model']}")
            gpu_snap = util.gpu_snapshot(gpu_mode)
            for suite_name, suite in suites:
                for name, spec in (suite.get("cases") or {}).items():
                    for r in range(repeat):
                        for row in pr.run_case(name, suite_name, t["model"], spec,
                                               {"depths": depths, "pp_sizes": pp_sizes,
                                                "force_depths": bool(args.depths), "force_pp": bool(args.pp_sizes)}):
                            row["tag"], row["run_id"], row["target"], row["model"] = tag, run_id, pr.base, t["model"]
                            row.setdefault("gpu", gpu_snap)
                            rows.append(row)
                            util.log("  " + report.fmt_row(row))
            util.merge_gpu(rows, util.gpu_snapshot(gpu_mode), gpu_snap)
    report.save(rows, out_dir, tag, run_id)
    print(f"\nSaved {len(rows)} rows -> {out_dir}/runs.csv; summarize with: summarize.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
