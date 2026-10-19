#!/usr/bin/env python3
"""Host-side verification: compares model answers against truth. Never runs in sandbox."""
import json, sys, os, glob

DIR = os.path.dirname(os.path.abspath(__file__))

def load_truth(bench):
    path = os.path.join(DIR, "truth", f"truth_{bench}.json")
    with open(path) as f:
        return {str(item["id"]): item["answer"] for item in json.load(f)}

def score_bench(answers, truth):
    correct = 0
    total = len(truth)
    missing = []
    for qid, expected in truth.items():
        if qid not in answers:
            missing.append(qid)
            continue
        ans_str = str(answers[qid]).strip()
        exp_str = str(expected).strip()
        if ans_str.lower() == exp_str.lower() or ans_str in exp_str:
            correct += 1
    return correct, total, missing

def main():
    model = sys.argv[1] if len(sys.argv) > 1 else None
    if not model:
        print("Usage: python3 verify.py <RUN_NAME>   (e.g. qwen3.8-27b-IQ3S_20250926-120000)")
        sys.exit(1)

    run_dir = os.path.join(DIR, "runs", model)
    if not os.path.isdir(run_dir):
        print(f"ERROR: {run_dir} does not exist. Run the benchmark first.")
        sys.exit(1)

    # Text benchmarks (answers in answers.json)
    ans_path = os.path.join(run_dir, "answers.json")
    text_benches = ["mmlu", "gsm8k", "arc", "hellaswag", "math", "winogrande", "bbh"]

    total_correct = 0
    total_questions = 0

    if os.path.isfile(ans_path):
        with open(ans_path) as f:
            all_answers = json.load(f)
        for bench in text_benches:
            if bench not in all_answers:
                print(f"  {bench}: MISSING (no answers)")
                continue
            truth = load_truth(bench)
            correct, total, missing = score_bench(all_answers[bench], truth)
            total_correct += correct
            total_questions += total
            status = "OK" if not missing else f"{len(missing)} missing"
            print(f"  {bench:12s}: {correct}/{total} ({correct*100//total}%) [{status}]")

    # Terminal benchmarks (per-run out files: runs/<RUN_NAME>/<bench>/<task>.txt)
    for bench, sub in [("terminal", "terminal"), ("terminal_hard", "terminal_hard")]:
        truth = load_truth(bench)
        correct = 0
        total = len(truth)
        for item in json.load(open(os.path.join(DIR, "truth", f"truth_{bench}.json"))):
            task_id = item["id"]
            task = task_id.split("/", 1)[1]
            out_file = os.path.join(run_dir, sub, f"{task}.txt")
            if not os.path.isfile(out_file):
                continue
            with open(out_file) as f:
                ans = f.read().strip()
            exp = str(item["answer"]).strip()
            if ans.lower() == exp.lower():
                correct += 1
        total_correct += correct
        total_questions += total
        print(f"  {bench:12s}: {correct}/{total} ({correct*100//total}%)")

    # HumanEval (code solutions)
    he_path = os.path.join(run_dir, "solutions.py")
    if os.path.isfile(he_path):
        print(f"  {'humaneval':12s}: (requires pytest execution, see score.py)")

    print(f"\n  OVERALL: {total_correct}/{total_questions} ({total_correct*100//total_questions}%)")

if __name__ == "__main__":
    main()
