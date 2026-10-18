#!/usr/bin/env python3
"""Summarize perf-bench results into a markdown pivot table."""
import argparse
import csv
import os
import statistics
import sys

COLS = ["tag", "model", "target"]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", "runs.csv"))
    ap.add_argument("--group-by", default="tag,model", help="columns to pivot on: any of " + ",".join(COLS))
    ap.add_argument("--metric", help="only this metric (default: all)")
    ap.add_argument("--last", type=int, help="keep only the N most recent run ids")
    ap.add_argument("--out", help="also write markdown here (default: <csv dir>/summary.md)")
    args = ap.parse_args()

    if not os.path.isfile(args.csv):
        print(f"no results file: {args.csv}", file=sys.stderr)
        return 2
    rows = list(csv.DictReader(open(args.csv, encoding="utf-8")))
    groups = [g.strip() for g in args.group_by.split(",") if g.strip() in COLS]
    if args.metric:
        rows = [r for r in rows if r["metric"] == args.metric]
    if args.last:
        run_ids = sorted({r["run_id"] for r in rows})[-args.last:]
        rows = [r for r in rows if r["run_id"] in run_ids]
    if not rows:
        print("no matching rows", file=sys.stderr)
        return 1

    cells, keys, labels = {}, set(), []
    for r in rows:
        label = " | ".join((r.get(g) or "-") for g in groups) or "all"
        if label not in labels:
            labels.append(label)
        key = (r["test"], r["phase"], int(float(r["depth"] or 0)), r["metric"])
        cells.setdefault(key, {}).setdefault(label, []).append(float(r["value"]))
        keys.add(key)

    out = ["| test | phase | depth | metric | " + " | ".join(labels) + " |",
           "|---|---|---|---|" + "---|" * len(labels)]
    for key in sorted(keys):
        test, phase, depth, metric = key
        vals = ["**%.1f**" % max(v) for v in cells[key].get(l, [])] or ["-"]
        cells_key = cells[key]
        cols = []
        for l in labels:
            vs = cells_key.get(l)
            cols.append("%.1f" % statistics.median(vs) if vs else "")
        best = max((max(vs) for vs in cells_key.values()), default=0)
        cols = [("**%.1f**" % float(c.strip("*")) if c and float(c.strip("*")) == best else c) for c in cols]
        out.append(f"| {test} | {phase} | {depth} | {metric} | " + " | ".join(cols) + " |")
    text = "\n".join(out) + "\n"
    print(text)
    path = args.out or os.path.join(os.path.dirname(os.path.abspath(args.csv)), "summary.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"(written {path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
