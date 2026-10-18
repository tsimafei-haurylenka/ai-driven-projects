"""Results persistence: per-run JSON + shared runs.csv + console formatting."""
import csv
import datetime as dt
import json
import os

FIELDS = ["ts", "tag", "run_id", "target", "model", "test", "phase", "depth",
          "metric", "value", "first_token_s", "detail", "gpu_delta_mb"]


def fmt_row(r):
    return (f"{r['test']:<28} d={r['depth']:<6} {r['metric']}={r['value']:<8} "
            f"{r.get('detail', '')}")


def save(rows, out_dir, tag, run_id):
    os.makedirs(out_dir, exist_ok=True)
    jpath = os.path.join(out_dir, f"run-{run_id}.json")
    with open(jpath, "w", encoding="utf-8") as f:
        json.dump({"run_id": run_id, "tag": tag, "rows": rows}, f, indent=1)
    cpath = os.path.join(out_dir, "runs.csv")
    new = not os.path.isfile(cpath)
    with open(cpath, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if new:
            w.writeheader()
        ts = dt.datetime.now().isoformat(timespec="seconds")
        for r in rows:
            r = {**r, "ts": r.get("ts", ts)}
            if isinstance(r.get("gpu_delta_mb"), dict):
                r["gpu_delta_mb"] = json.dumps(r["gpu_delta_mb"])
            w.writerow({k: r.get(k, "") for k in FIELDS})
    return jpath, cpath
