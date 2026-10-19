"""Aggregate all bench/results/results_*.json into one comparison table.

Fixed total width = 226 chars. Long model names, run ids, and notes are truncated with an ellipsis.
Meta columns: Ctx = context window in use (k tokens), Cmp = auto-detected context compactions,
Seed = generation seed pinning sampling ("-" = random).
"""
import json, os, glob

OUT = os.path.dirname(os.path.abspath(__file__))
BENCHES = ["MMLU", "GSM8K", "HellaSwag", "ARC-Chall", "MATH",
           "WinoGrande", "BBH", "HumanEval", "Terminal", "TermHard"]

FIXED_WIDTH = 226
MODEL_W = 24
RUNID_W = 24
BENCH_W = 7
OVERALL_W = 8
TIME_W = 12
CTX_W = 7
CMP_W = 4
SEED_W = 9
NOTE_W = 67
# MODEL_W + RUNID_W + BENCH_W*len(BENCHES) + OVERALL_W + TIME_W + CTX_W + CMP_W + SEED_W + NOTE_W + 1 (sep) == 226

# short column labels (fit in BENCH_W)
SHORT = {
    "MMLU": "MMLU", "GSM8K": "GSM8K", "HellaSwag": "Hella",
    "ARC-Chall": "ARC", "MATH": "MATH", "WinoGrande": "Wino",
    "BBH": "BBH", "HumanEval": "HumEv", "Terminal": "Term",
    "TermHard": "TermH",
}


def trunc(s, w):
    if len(s) <= w:
        return s
    return s[:w - 1] + "…"


def load_timing(model, d):
    return d.get("overall", {}).get("time_s")


def fmt_time(t):
    """Time is always seconds — no rounding to minutes/hours."""
    if t is None:
        return "-"
    return f"{t:.1f}s"


def load_meta(model):
    fp = os.path.join(OUT, "meta", f"meta_{model}.json")
    if os.path.exists(fp):
        with open(fp) as f:
            return json.load(f)
    return None


def main():
    files = sorted(glob.glob(os.path.join(OUT, "results", "results_*.json")))
    if not files:
        print("no results/results_*.json found")
        return
    rows = {}
    for fp in files:
        with open(fp) as f:
            d = json.load(f)
        rows[d["model"]] = d

    title = "COMPARISON (same fixed sample, seed=1234)"
    print("=" * FIXED_WIDTH)
    print(title)
    print("=" * FIXED_WIDTH)

    hdr = f"{'model':<{MODEL_W}s}"
    hdr += f"{'run id':<{RUNID_W}s}"
    for b in BENCHES:
        hdr += f"{SHORT[b]:>{BENCH_W}s}"
    hdr += f"{'OVERALL':>{OVERALL_W}s}"
    hdr += f"{'Time':>{TIME_W}s}"
    hdr += f"{'Ctx':>{CTX_W}s}"
    hdr += f"{'Cmp':>{CMP_W}s}"
    hdr += f"{'Seed':>{SEED_W}s}"
    hdr += f" {'Note':<{NOTE_W}s}"
    print(hdr)
    print("-" * FIXED_WIDTH)

    for run_name, d in rows.items():
        # RUN_NAME = <MODEL>_<RUN_ID>; neither part contains an underscore (run.sh makes SAFE_MODEL with / -> -)
        model, _, run_id = run_name.partition("_")
        row = f"{trunc(model, MODEL_W):<{MODEL_W}s}"
        row += f"{trunc(run_id, RUNID_W):<{RUNID_W}s}"
        for b in BENCHES:
            v = d["per_benchmark"].get(b)
            row += f"{v['acc']:>{BENCH_W - 1}.1f}%" if v else f"{'-':>{BENCH_W}s}"
        row += f"{d['overall']['acc']:>{OVERALL_W - 1}.1f}%"
        row += f"{fmt_time(load_timing(run_name, d)):>{TIME_W}s}"
        meta = load_meta(run_name) or {}
        ctx = meta.get("context_size")
        row += f"{(f'{ctx // 1000}k' if ctx else '-'):>{CTX_W}s}"
        cmp_ = meta.get("compactions")
        row += f"{(str(cmp_) if cmp_ is not None else '-'):>{CMP_W}s}"
        seed = meta.get("seed")
        row += f"{(str(seed) if seed is not None else '-'):>{SEED_W}s}"
        note = meta.get("notes")
        row += f" {trunc(note, NOTE_W):<{NOTE_W}s}" if note else f" {'-':<{NOTE_W}s}"
        print(row)

    print("=" * FIXED_WIDTH)


if __name__ == "__main__":
    main()
