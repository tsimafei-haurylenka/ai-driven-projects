"""PHASE 3 — SCORING (deterministic).

Usage:
    ./.benchenv/bin/python bench/score.py <RUN_NAME>

RUN_NAME is the run directory name, e.g. qwen3.8-27b-IQ3S_20250926-120000
(= <MODEL>_<RUN_ID> as created by run.sh). Every run is scored independently.

Reads:
    bench/runs/<RUN_NAME>/answers.json       (model's answers, blind)
    bench/runs/<RUN_NAME>/solutions.py       (model's HumanEval code)
    bench/runs/<RUN_NAME>/terminal[_hard]/<task>.txt   (terminal outputs)
    bench/truth/truth_<bench>.json          (ground truth)
Writes:
    bench/results/results_<RUN_NAME>.json
Prints a table.

This script NEVER lets the model see the truth; it only compares recorded
answers against ground truth. Run it AFTER the model has finished answering.
"""
import json, os, sys, re, subprocess, tempfile, inspect, importlib.util

OUT = os.path.dirname(os.path.abspath(__file__))

def load(name):
    with open(os.path.join(OUT, "truth", name)) as f:
        return json.load(f)

# ---------- numeric helpers ----------
def _frac_to_num(s):
    s = s.strip()
    m = re.match(r"^\\\\frac\{([^}]*)\}\{([^}]*)\}$", s)
    if m:
        try:
            return float(m.group(1)) / float(m.group(2))
        except Exception:
            return None
    return None

def to_number(s):
    """Best-effort numeric parse of a math answer string."""
    if s is None:
        return None
    s = str(s).strip().replace("$", "").replace(",", "").replace(" ", "")
    s = s.replace("\\%", "%").replace("\\times", "*").replace("\\cdot", "*")
    s = s.replace("\\pi", "3.141592653589793")
    f = _frac_to_num(s)
    if f is not None:
        return f
    # handle a/b
    if re.fullmatch(r"-?\d+/\d+", s):
        a, b = s.split("/")
        try:
            return float(a) / float(b)
        except Exception:
            return None
    try:
        return float(s)
    except Exception:
        return None

def norm_str(s):
    return str(s).strip().lower().replace(" ", "")

# ---------- per-benchmark scorers ----------
def score_mc(answers, truth, key="answer"):
    correct = 0
    for t in truth:
        tid = str(t["id"])
        my = answers.get(tid)
        if my is None:
            continue
        if norm_str(my) == norm_str(t[key]):
            correct += 1
    return correct, len(truth)

def score_numeric(answers, truth, key="answer"):
    correct = 0
    for t in truth:
        tid = str(t["id"])
        my = answers.get(tid)
        if my is None:
            continue
        m, g = to_number(my), to_number(t[key])
        if m is not None and g is not None and abs(m - g) < 1e-6:
            correct += 1
        elif norm_str(my) == norm_str(t[key]):
            correct += 1
    return correct, len(truth)

def score_terminal(out_dir, truth):
    """Terminal-agent benchmark. We verify the ACTUAL artifact:
    runs/<RUN_NAME>/<terminal|terminal_hard>/<task>.txt."""
    correct = 0
    for t in truth:
        tid = t["id"]
        task = tid.split("/", 1)[1]
        out_path = os.path.join(out_dir, f"{task}.txt")
        if not os.path.exists(out_path):
            continue
        with open(out_path) as f:
            my = f.read().strip()
        g = t["answer"]
        m, gv = to_number(my), to_number(g)
        if m is not None and gv is not None and abs(m - gv) < 1e-6:
            correct += 1
        elif norm_str(my) == norm_str(g):
            correct += 1
    return correct, len(truth)

def score_humaneval(solutions_path, truth):
    if not os.path.exists(solutions_path):
        return [(t["id"], False, "no solutions file") for t in truth]
    spec = importlib.util.spec_from_file_location("mysol", solutions_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    results = []
    for t in truth:
        ep = t["entry_point"]
        fn = getattr(mod, ep, None)
        if fn is None:
            results.append((t["id"], False, "function not defined"))
            continue
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write("from typing import List\n")
            f.write(inspect.getsource(fn))
            f.write("\n\n")
            f.write(t["test"])
            f.write(f"\ncheck({ep})\n")
            fname = f.name
        try:
            r = subprocess.run([sys.executable, fname], capture_output=True,
                               text=True, timeout=30)
            ok = (r.returncode == 0)
            err = (r.stderr.strip() or r.stdout.strip())[:200]
        except subprocess.TimeoutExpired:
            ok, err = False, "timeout"
        finally:
            os.unlink(fname)
        results.append((t["id"], ok, err))
    return results

def load_time(run_dir, test):
    """Read time from runs/<RUN_NAME>/time_<test>.txt, return seconds or None."""
    fp = os.path.join(run_dir, f"time_{test}.txt")
    if not os.path.exists(fp):
        return None
    with open(fp) as f:
        content = f.read().strip()
    # Try to parse a number from the file
    m = re.search(r"(\d+\.?\d*)", content)
    if m:
        return float(m.group(1))
    return None

BENCH_NAMES = ["mmlu", "gsm8k", "hellaswag", "arc", "math", "winogrande", "bbh", "humaneval", "terminal", "terminal_hard"]

def main():
    if len(sys.argv) < 2:
        print("usage: score.py <model_name>")
        sys.exit(1)
    model = sys.argv[1]  # RUN_NAME
    run_dir = os.path.join(OUT, "runs", model)
    ans_path = os.path.join(run_dir, "answers.json")
    sol_path = os.path.join(run_dir, "solutions.py")
    if not os.path.exists(ans_path):
        print(f"missing {ans_path} (run it first: ./run.sh <MODEL> [RUN_ID])")
        sys.exit(1)
    with open(ans_path) as f:
        A = json.load(f)

    print("=" * 64)
    print(f"RESULTS — model: {model}")
    print("=" * 64)
    summary = {}

    def report(name, correct, total, kind, test_name=None):
        acc = 100 * correct / total if total else 0
        t = load_time(run_dir, test_name) if test_name else None
        t_str = f"  [{t:.1f}s]" if t is not None else ""
        print(f"{name:12s} {correct:4d}/{total:<4d} = {acc:6.1f}%   ({kind}){t_str}")
        entry = {"correct": correct, "total": total, "acc": round(acc, 2)}
        if t is not None:
            entry["time_s"] = t
        summary[name] = entry

    # MMLU
    c, n = score_mc(A.get("mmlu", {}), load("truth_mmlu.json"))
    report("MMLU", c, n, "MC", "mmlu")
    # GSM8K
    c, n = score_numeric(A.get("gsm8k", {}), load("truth_gsm8k.json"))
    report("GSM8K", c, n, "math", "gsm8k")
    # HellaSwag
    c, n = score_mc(A.get("hellaswag", {}), load("truth_hellaswag.json"))
    report("HellaSwag", c, n, "MC", "hellaswag")
    # ARC
    c, n = score_mc(A.get("arc", {}), load("truth_arc.json"))
    report("ARC-Chall", c, n, "MC", "arc")
    # MATH
    c, n = score_numeric(A.get("math", {}), load("truth_math.json"))
    report("MATH", c, n, "math", "math")
    # WinoGrande
    c, n = score_mc(A.get("winogrande", {}), load("truth_winogrande.json"))
    report("WinoGrande", c, n, "MC", "winogrande")
    # BBH
    c, n = score_mc(A.get("bbh", {}), load("truth_bbh.json"))
    report("BBH", c, n, "exact", "bbh")
    # HumanEval
    res = score_humaneval(sol_path, load("truth_humaneval.json"))
    passed = sum(1 for _, ok, _ in res if ok)
    n = len(res)
    report("HumanEval", passed, n, "code", "humaneval")
    fails = [tid for tid, ok, _ in res if not ok]
    if fails:
        print(f"  HumanEval fails: {fails}")
    # Terminal (agent)
    if os.path.exists(os.path.join(OUT, "truth", "truth_terminal.json")):
        c, n = score_terminal(os.path.join(run_dir, "terminal"), load("truth_terminal.json"))
        report("Terminal", c, n, "agent", "terminal")
    # Terminal-hard (agent)
    if os.path.exists(os.path.join(OUT, "truth", "truth_terminal_hard.json")):
        c, n = score_terminal(os.path.join(run_dir, "terminal_hard"), load("truth_terminal_hard.json"))
        report("TermHard", c, n, "agent", "terminal_hard")

    # overall
    tot_c = sum(v["correct"] for v in summary.values())
    tot_n = sum(v["total"] for v in summary.values())
    tot_time = sum(v.get("time_s", 0) for v in summary.values())
    print("-" * 64)
    time_str = f"  [{tot_time:.1f}s]" if any(v.get("time_s") for v in summary.values()) else ""
    print(f"OVERALL      {tot_c}/{tot_n} = {100*tot_c/tot_n:.1f}%{time_str}")

    overall = {"correct": tot_c, "total": tot_n, "acc": round(100 * tot_c / tot_n, 2)}
    if any(v.get("time_s") for v in summary.values()):
        overall["time_s"] = round(tot_time, 1)

    results_dir = os.path.join(OUT, "results")
    os.makedirs(results_dir, exist_ok=True)
    with open(os.path.join(results_dir, f"results_{model}.json"), "w") as f:
        json.dump({"model": model, "per_benchmark": summary,
                   "overall": overall},
                  f, ensure_ascii=False, indent=2)
    print(f"[saved] results/results_{model}.json")

if __name__ == "__main__":
    main()
