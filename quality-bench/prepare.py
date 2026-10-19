"""PHASE 1 — PREPARATION (deterministic, reproducible).

Downloads benchmarks from HuggingFace, samples a FIXED number of questions with a
FIXED seed, and writes:
  - questions/questions_<bench>.json : BLIND questions (NO answers). The model reads these.
  - truth/truth_<bench>.json          : ground truth. The model MUST NOT read these.

Run:  ./.benchenv/bin/python bench/prepare.py
Change N_* to resize the sample (keep the seed fixed for cross-model comparability).
"""
import json, os, random
from datasets import load_dataset

OUT = os.path.dirname(os.path.abspath(__file__))
SEED = 1234

# ---- sample sizes (increase these for a bigger, more stable test) ----
N_MMLU = 200
N_GSM8K = 100
N_HELLASWAG = 200
N_ARC = 200
N_HUMANEVAL = 30
N_MATH = 100
N_WINOGRANDE = 200
N_BBH = 100

def save(name, questions, truth):
    os.makedirs(os.path.join(OUT, "questions"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "truth"), exist_ok=True)
    with open(os.path.join(OUT, "questions", f"questions_{name}.json"), "w") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, "truth", f"truth_{name}.json"), "w") as f:
        json.dump(truth, f, ensure_ascii=False, indent=2)
    print(f"[ok] {name}: {len(questions)} items")

def do_mmlu(n):
    ds = load_dataset("cais/mmlu", "all", split="test")
    idx = random.sample(range(len(ds)), n)
    qs, tr = [], []
    for i in idx:
        r = ds[i]
        qs.append({"id": i, "subject": r["subject"], "question": r["question"],
                   "choices": r["choices"]})
        tr.append({"id": i, "answer": r["answer"]})
    save("mmlu", qs, tr)

def do_gsm8k(n):
    ds = load_dataset("openai/gsm8k", "main", split="test")
    idx = random.sample(range(len(ds)), n)
    qs, tr = [], []
    for i in idx:
        r = ds[i]
        gt = r["answer"].split("####")[-1].strip().replace(",", "")
        qs.append({"id": i, "question": r["question"]})
        tr.append({"id": i, "answer": gt})
    save("gsm8k", qs, tr)

def do_hellaswag(n):
    ds = load_dataset("Rowan/hellaswag", split="validation")
    idx = random.sample(range(len(ds)), n)
    qs, tr = [], []
    for i in idx:
        r = ds[i]
        ctx = r["ctx_a"] + " " + r["ctx_b"]
        qs.append({"id": i, "ctx": ctx, "endings": r["endings"]})
        tr.append({"id": i, "answer": r["label"]})
    save("hellaswag", qs, tr)

def do_arc(n):
    ds = load_dataset("allenai/ai2_arc", "ARC-Challenge", split="test")
    idx = random.sample(range(len(ds)), n)
    qs, tr = [], []
    for i in idx:
        r = ds[i]
        labels = r["choices"]["label"]
        texts = r["choices"]["text"]
        pairs = dict(zip(labels, texts))
        qs.append({"id": i, "question": r["question"], "choices": pairs})
        tr.append({"id": i, "answer": r["answerKey"]})
    save("arc", qs, tr)

def do_humaneval(n):
    ds = load_dataset("openai/openai_humaneval", split="test")
    idx = random.sample(range(len(ds)), n)
    qs, tr = [], []
    for i in idx:
        r = ds[i]
        qs.append({"id": r["task_id"], "prompt": r["prompt"]})
        tr.append({"id": r["task_id"], "test": r["test"],
                   "entry_point": r["entry_point"]})
    save("humaneval", qs, tr)

def do_math(n):
    import re
    ds = load_dataset("dim/competition_math", split="train")
    idx = random.sample(range(len(ds)), n)
    qs, tr = [], []
    for i in idx:
        r = ds[i]
        m = re.findall(r"\\boxed\{([^}]*)\}", r["solution"])
        ans = m[-1].strip() if m else ""
        qs.append({"id": i, "question": r["problem"], "level": r["level"]})
        tr.append({"id": i, "answer": ans})
    save("math", qs, tr)

def do_winogrande(n):
    # test split answers are withheld in this mirror; train split has them (1 or 2)
    ds = load_dataset("allenai/winogrande", "winogrande_l", split="train")
    idx = random.sample(range(len(ds)), n)
    qs, tr = [], []
    for i in idx:
        r = ds[i]
        qs.append({"id": i, "sentence": r["sentence"],
                   "options": [r["option1"], r["option2"]]})
        tr.append({"id": i, "answer": r["answer"].strip()})
    save("winogrande", qs, tr)

def do_bbh(n):
    # BBH "web_of_lies" subset (logical deduction, clean Yes/No answer)
    ds = load_dataset("lukaemon/bbh", "web_of_lies", split="test")
    idx = random.sample(range(len(ds)), n)
    qs, tr = [], []
    for i in idx:
        r = ds[i]
        qs.append({"id": i, "input": r["input"]})
        tr.append({"id": i, "answer": r["target"].strip()})
    save("bbh", qs, tr)

if __name__ == "__main__":
    random.seed(SEED)
    do_mmlu(N_MMLU)
    random.seed(SEED)
    do_gsm8k(N_GSM8K)
    random.seed(SEED)
    do_hellaswag(N_HELLASWAG)
    random.seed(SEED)
    do_arc(N_ARC)
    random.seed(SEED)
    do_humaneval(N_HUMANEVAL)
    random.seed(SEED)
    do_math(N_MATH)
    random.seed(SEED)
    do_winogrande(N_WINOGRANDE)
    random.seed(SEED)
    do_bbh(N_BBH)
    print("DONE")
