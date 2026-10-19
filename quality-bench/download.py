"""Download a fixed sample of well-known LLM benchmarks from HuggingFace.

Saves, per benchmark:
  - questions/questions_<name>.json : the questions to answer (what the model reads)
  - truth/truth_<name>.json          : ground-truth for scoring (kept separate)

Sample sizes are small on purpose so a single model can actually answer all of
them by hand. Seed is fixed for reproducibility.
"""
import json, os, random
from datasets import load_dataset

OUT = os.path.dirname(os.path.abspath(__file__))
SEED = 1234
random.seed(SEED)

def save(name, questions, truth):
    os.makedirs(os.path.join(OUT, "questions"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "truth"), exist_ok=True)
    with open(os.path.join(OUT, "questions", f"questions_{name}.json"), "w") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, "truth", f"truth_{name}.json"), "w") as f:
        json.dump(truth, f, ensure_ascii=False, indent=2)
    print(f"[ok] {name}: {len(questions)} items")

# ---------------- MMLU (general knowledge, 4-way MC) ----------------
def do_mmlu(n=40):
    ds = load_dataset("cais/mmlu", "all", split="test")
    idx = random.sample(range(len(ds)), n)
    qs, tr = [], []
    for i in idx:
        r = ds[i]
        qs.append({"id": i, "subject": r["subject"], "question": r["question"],
                   "choices": r["choices"]})
        tr.append({"id": i, "answer": r["answer"]})
    save("mmlu", qs, tr)

# ---------------- GSM8K (grade-school math, free-form -> number) ----------------
def do_gsm8k(n=20):
    ds = load_dataset("openai/gsm8k", "main", split="test")
    idx = random.sample(range(len(ds)), n)
    qs, tr = [], []
    for i in idx:
        r = ds[i]
        # ground truth is the number after ####
        gt = r["answer"].split("####")[-1].strip().replace(",", "")
        qs.append({"id": i, "question": r["question"]})
        tr.append({"id": i, "answer": gt})
    save("gsm8k", qs, tr)

# ---------------- HellaSwag (commonsense, 4-way MC) ----------------
def do_hellaswag(n=40):
    ds = load_dataset("Rowan/hellaswag", split="validation")
    idx = random.sample(range(len(ds)), n)
    qs, tr = [], []
    for i in idx:
        r = ds[i]
        ctx = r["ctx_a"] + " " + r["ctx_b"]
        qs.append({"id": i, "ctx": ctx, "endings": r["endings"]})
        tr.append({"id": i, "answer": r["label"]})
    save("hellaswag", qs, tr)

# ---------------- ARC (science MC) ----------------
def do_arc(n=40):
    ds = load_dataset("allenai/ai2_arc", "ARC-Challenge", split="test")
    idx = random.sample(range(len(ds)), n)
    qs, tr = [], []
    for i in idx:
        r = ds[i]
        labels = r["choices"]["label"]   # e.g. ["A","B","C","D"]
        texts = r["choices"]["text"]
        pairs = dict(zip(labels, texts))
        qs.append({"id": i, "question": r["question"], "choices": pairs})
        tr.append({"id": i, "answer": r["answerKey"]})
    save("arc", qs, tr)

# ---------------- HumanEval (code generation) ----------------
def do_humaneval(n=15):
    ds = load_dataset("openai/openai_humaneval", split="test")
    idx = random.sample(range(len(ds)), n)
    qs, tr = [], []
    for i in idx:
        r = ds[i]
        qs.append({"id": r["task_id"], "prompt": r["prompt"]})
        tr.append({"id": r["task_id"], "test": r["test"],
                  "entry_point": r["entry_point"]})
    save("humaneval", qs, tr)

if __name__ == "__main__":
    do_mmlu()
    do_gsm8k()
    do_hellaswag()
    do_arc()
    do_humaneval()
    print("DONE")
