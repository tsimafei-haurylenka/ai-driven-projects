"""Wipe benchmark runs so the comparison table starts from scratch.

Usage:
    python3 clean.py --list
    python3 clean.py <RUN_NAME> [<RUN_NAME> ...] [--purge] [--yes]
    python3 clean.py --all [--purge] [--yes]

Removes a run's artifacts: runs/<RUN>/, results/results_<RUN>.json,
meta/meta_<RUN>.json, .stage/<RUN>/. Default MOVES everything to .prev_runs/
(restorable); --purge deletes outright. Questions and truth are never touched.
"""
import argparse, glob, os, shutil, sys

OUT = os.path.dirname(os.path.abspath(__file__))
DIRS = ["runs", "results", "meta"]

def paths_for(run):
    return [
        os.path.join(OUT, "runs", run),
        os.path.join(OUT, "results", f"results_{run}.json"),
        os.path.join(OUT, "meta", f"meta_{run}.json"),
        os.path.join(OUT, ".stage", run),
    ]

def all_runs():
    runs = set()
    for d in glob.glob(os.path.join(OUT, "runs", "*")):
        if os.path.isdir(d):
            runs.add(os.path.basename(d))
    for fp in glob.glob(os.path.join(OUT, "results", "results_*.json")):
        runs.add(os.path.basename(fp)[len("results_"):-len(".json")])
    return sorted(runs)

def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="*", help="run name(s) to wipe")
    ap.add_argument("--all", action="store_true", help="all runs")
    ap.add_argument("--list", action="store_true", help="list runs and exit")
    ap.add_argument("--purge", action="store_true", help="delete instead of moving to .prev_runs/")
    ap.add_argument("--yes", action="store_true", help="no confirmation prompt")
    a = ap.parse_args()

    if a.list:
        for r in all_runs():
            scored = os.path.exists(os.path.join(OUT, "results", f"results_{r}.json"))
            print(f"{'[scored]' if scored else '[raw]   '} {r}")
        return

    targets = all_runs() if a.all else a.runs
    if a.all and not targets:
        print("nothing to clean — no runs found")
        return
    if not targets:
        ap.error("no run(s) given (use run names or --all; --list to see them)")

    files = [p for r in targets for p in paths_for(r) if os.path.exists(p)]
    print(f"{len(targets)} run(s), {len(files)} files/dirs to {'DELETE' if a.purge else 'move to .prev_runs/'}:")
    for p in files:
        print("  ", os.path.relpath(p, OUT))
    if not (a.yes or not sys.stdin.isatty()):
        if input("proceed? [y/N] ").strip().lower() != "y":
            print("aborted")
            return

    for p in files:
        if a.purge:
            shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
        else:
            dst = os.path.join(OUT, ".prev_runs", os.path.relpath(p, OUT))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(p, dst)
    print("done — compare.py/report.py now show the remaining runs")

if __name__ == "__main__":
    main()
