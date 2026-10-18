"""Helpers: structured config reading (JSON, optional YAML), logging, GPU snapshots."""
import json
import os
import shutil
import subprocess
import sys


def die(msg, code=2):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def log(msg):
    print(msg, flush=True)


def first_existing(*paths):
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None


def read_structured(path):
    """Read .json natively; .yaml/.yml via PyYAML when installed."""
    if not path or not os.path.isfile(path):
        return None
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml
        except ImportError:
            die(f"PyYAML is required to read {path} (or convert it to .json)")
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def gpu_snapshot(mode):
    """Best-effort local GPU stats: nvidia-smi, then rocm-smi; never fails the run."""
    if mode == "off":
        return {}
    snap = {}
    try:
        if shutil.which("nvidia-smi"):
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total,memory.used", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=15).stdout
            for i, line in enumerate(out.strip().splitlines()):
                n, tot, used = [x.strip() for x in line.split(",")]
                snap[f"gpu{i}"] = {"vendor": "nvidia", "name": n, "total_mb": float(tot), "used_mb": float(used)}
        elif shutil.which("rocm-smi"):
            out = subprocess.run(["rocm-smi", "--showmeminfo", "vram"], capture_output=True, text=True, timeout=15).stdout
            for line in out.splitlines():
                if "GPU[" in line and "Total" in line and "B)" in line:
                    idx = int(line.split("[")[1].split("]")[0])
                    snap.setdefault(f"gpu{idx}", {"vendor": "amd"})["total_mb"] = float(line.split()[-1]) / 1048576
                elif "GPU[" in line and "Used" in line and "B)" in line:
                    idx = int(line.split("[")[1].split("]")[0])
                    snap.setdefault(f"gpu{idx}", {"vendor": "amd"})["used_mb"] = float(line.split()[-1]) / 1048576
    except Exception as e:  # any probe failure is non-fatal
        snap["probe_error"] = str(e)
    return snap


def merge_gpu(rows, after, before):
    for r in rows:
        r.setdefault("gpu", {}); r["gpu_after"] = after
        if before and after:
            deltas = {}
            for k, v in after.items():
                b = (before or {}).get(k)
                if isinstance(v, dict) and isinstance(b, dict) and "used_mb" in v and "used_mb" in b:
                    deltas[k] = round(v["used_mb"] - b["used_mb"], 1)
            r["gpu_delta_mb"] = deltas
