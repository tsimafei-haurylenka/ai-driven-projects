# Benchmark Protocol (for the model under test)

You run **inside a Docker container** (`bench-sandbox`). Each category is its own
container: your session holds **exactly one benchmark**, and other benchmarks are
run in separate sessions you never see. Isolation is enforced by mounts: anything
not mounted does not exist in the container. The operator starts each session with
`./run.sh <MODEL_NAME>` and scores afterwards with host-only tools you never touch.

## What exists inside the container

| Path | What it is | Access |
|---|---|---|
| `/questions/` | `questions_<bench>.json` for YOUR benchmark only (no answers inside) | read-only |
| `/work/` | your output dir (`→ runs/<MODEL>_<RUN_ID>/` on host, shared across sessions of this run) | writable |
| `/tasks/` | clean terminal task dirs — **only present in Terminal/TerminalHard sessions** | writable |
| `/bench/` | helper scripts + this instruction (`INSTRUCTIONS.md`) | read-only |

## The one rule

**Answer the questions of YOUR benchmark and write your results. That is all.**

- Read ONLY `INSTRUCTIONS.md` (this file) and `/questions/questions_<bench>.json`.
- Answer each question **exactly once**. No re-computing, no iterating on a score, no fixing after seeing a result.
- Do ONLY your benchmark this session (the launch prompt names it). Ignore anything in this file about other benchmarks — they are separate sessions.

### Where your answers go (all under `/work/`)

| Benchmark | File in `/work/` |
|---|---|
| MMLU, GSM8K, ARC, HellaSwag, MATH, WinoGrande, BBH | `answers.json`, key = your bench name (`mmlu`, `gsm8k`, ...) |
| HumanEval | `solutions.py` (30 functions) — **NOT** `answers.json` |
| Terminal / TerminalHard | `out.txt` inside each `/tasks/.../<sandbox>/` dir of YOUR tasks |

Plus one timing file: `/work/time_<bench>.txt`, written at the very end via `_time.py`.

## Hard rules (violating any of them invalidates the run)

1. **Batches of exactly 10.** Load no more than 10 questions at a time
   (`_show_batch.py <bench> <start> 10`). Answer only what is shown in the current batch.
2. **One answer per question.** No edits after you record it.
3. **Your session = one benchmark.** Finish it completely, then stop. Do not attempt other benchmarks — they do not exist in this container anyway.

## Benchmarks (each runs as its own session)

| Benchmark | n | Write answers to | Key / file |
|---|---|---|---|
| MMLU | 200 | `answers.json` via `_append_batch.py mmlu '<json>'` | `mmlu` |
| GSM8K | 100 | same, key `gsm8k` | `gsm8k` |
| ARC-Challenge | 200 | same, key `arc` | `arc` |
| HellaSwag | 200 | same, key `hellaswag` | `hellaswag` |
| MATH (olympiad math — its own benchmark, not part of GSM8K) | 100 | same, key `math` | `math` |
| WinoGrande | 200 | same, key `winogrande` | `winogrande` |
| BBH (web_of_lies) | 100 | same, key `bbh` | `bbh` |
| HumanEval | 30 | **`solutions.py` — NOT `_append_batch.py`** | see section below |
| Terminal (agent) | 50 | `out.txt` in each `/tasks/terminal_sandboxes/<sandbox>/` | see terminal section |
| TerminalHard (agent) | 50 | `out.txt` in each `/tasks/terminal_sandboxes_hard/<sandbox>/` | same |

## Answer format per benchmark

### MMLU — key `mmlu`
- **Integer 0, 1, 2 or 3** (0-based option index). Example: `{"234": 2}`.

### GSM8K — key `gsm8k`
- **String with the final number** (no words, no units). Example: `{"3": "42"}`.

### ARC-Challenge — key `arc`
- **String — the option label from that question's `choices` keys.** Most are `"A"`–`"D"`,
  some are numeric `"1"`–`"4"`. Read the keys in the question, do not assume letters.
- Example: `{"ARC-Challenge_tr_0": "B"}` or `{"ARC-Challenge_va_1": "3"}`.

### HellaSwag — key `hellaswag`
- **Integer 0, 1, 2 or 3** (0-based option index). Example: `{"2451": 1}`.

### MATH — key `math`
- **String with the final answer**: number, fraction, or expression.
  Fractions as `"a/b"` (e.g. `"3/4"`), not decimals. Expressions allowed:
  `"42"`, `"1/4"`, `"2*sqrt(2)"`, `"pi/2"`. Example: `{"6374": "36"}`.

### WinoGrande — key `winogrande`
- **String `"1"` or `"2"` — 1-based!** First option (`options[0]`) = `"1"`, second = `"2"`.
- **Never write `"0"`** — there is no `"0"` answer in WinoGrande. This is the most common mistake: intuition says 0-based, but here it starts at 1.
- Example: `{"7220": "2"}` (second option correct) or `{"7220": "1"}` (first).

### BBH (web_of_lies) — key `bbh`
- **String `"Yes"` or `"No"`** (case-insensitive at scoring time).
- Mechanics: "A says B tells the truth" → A and B same type; "A says B lies" → different types. Walk the chain from what is known.
- Example: `{"0": "yes"}`.

### HumanEval — see the section below (30 functions into `/work/solutions.py`). Functions already defined in `/work/solutions.py` are locked: add only the missing ones, never rewrite existing code.

## Process for your benchmark

1. Record the start time **before** you begin: `date +%s`. Remember this number; `_time.py` needs it at the end.
2. Run `python3 /bench/_todo.py <bench>` — it prints how many questions are already answered and how many remain. A previous session may have answered some; those answers are **locked**: they are final, you must not re-derive or overwrite them.
3. Read questions in batches of 10 over the full list: `_show_batch.py <bench> 0 10`, then `10 10`, `20 10`, ... The helper **hides already-answered questions inside each window** — a window may show fewer than 10 or even 0 questions; that is normal, just move to the next window. Continue until every question has an answer (`_todo.py <bench>` reports `remaining 0`).
4. Answer each question once, in the format from "Answer format".
5. Write your answers (see table above for which mechanism per benchmark).
6. Run the **completion check** below; only proceed when it prints `OK`.
7. Finish with `_time.py <bench> <start_epoch>` and stop working.

### Completion check (mandatory before you finish)

The seven text benchmarks — answer count must equal question count:

```bash
python3 -c "import json; b='<bench>'; nq=len(json.load(open(f'/questions/questions_{b}.json'))); na=len(json.load(open('/work/answers.json')).get(b,{})); print('OK' if na==nq else f'MISSING: {na}/{nq}')"
```

(Replace `<bench>` with your bench key — `mmlu`, `gsm8k`, ... If it prints MISSING, continue from where you stopped — `_append_batch.py` only ever records answers that are not yet present; it refuses to overwrite. The same status is available as `python3 /bench/_todo.py <bench>`.)

HumanEval — function count must be 30:

```bash
grep -c "^def " /work/solutions.py    # must print 30
```

Terminal / TerminalHard — every task dir of your session has an `out.txt`:

```bash
python3 - <<'EOF'
import json, os
qf = [p for p in ('/questions/questions_terminal.json', '/questions/questions_terminal_hard.json') if os.path.exists(p)][0]
qs = json.load(open(qf))
roots = [d for d in ('/tasks/terminal_sandboxes', '/tasks/terminal_sandboxes_hard') if os.path.isdir(d)]
missing = []
for q in qs:
    ok = any(os.path.exists(os.path.join(r, q['sandbox'], 'out.txt')) for r in roots)
    missing.append(q['sandbox']) if not ok else None
print('OK' if not missing else 'MISSING out.txt: %s' % missing)
EOF
```

If it prints MISSING, do those tasks now.

## HumanEval: how to write solutions (SPECIAL FORMAT)

**HumanEval is NOT JSON answers.** Write code into `/work/solutions.py` (one file, 30 functions).
> **Do NOT use `_append_batch.py` for HumanEval.** If you put code in `answers.json`, scoring will not see it and you get 0/30. Scoring reads only `solutions.py`.

A normal Python file with the functions appended one by one, each complete (with a body),
named exactly as the task's `entry_point`:

```python
from typing import List

def reverse_delete(s, c):
    for ch in c:
        s = s.replace(ch, "")
    return (s, s == s[::-1])

def sum_of_first_n(n):
    return n * (n + 1) // 2
```

### Writing rules

- Function name = the task's `entry_point`. Write the **full** function with a body.
- If the prompt already gives a helper function (e.g. `encode_cyclic` in HumanEval/38), write **only** the function you are asked for.
- Put library imports (`hashlib`, `math`, `re`, ...) **inside the function body**, not at the top of the file. (Exception: `from typing import List` at the top is allowed.)
- Signatures may use only `List`, `int`, `str`, `float`, `bool` and built-ins. Do NOT use `Tuple`, `Any`, `Dict` — they cause `NameError` at check time.

## Terminal / TerminalHard: details (agent tasks)

You act **as an agent in a terminal**:
- Each task is a dir `/tasks/terminal_sandboxes/<sandbox>/` (Terminal session) or
  `/tasks/terminal_sandboxes_hard/<sandbox>/` (TermHard session), with the starting files
  (clean copy — no answer file, no other model's output). The list of your tasks and their
  `task.md` text is in `/questions/questions_terminal.json` / `_hard`.json.
- Read **only** `task.md` inside that dir.
- Work with shell commands on the data files there (`grep`, `awk`, `sed`, `sort`, `uniq`,
  `find`, `jq`, `tar`, `git`, `python3`, ...).
- Write the final answer to `out.txt` **in that same dir** (one line, no extra text).
- A sandbox that **already contains `out.txt`** is DONE (from a previous attempt): never open it again, never redo it, never overwrite `out.txt`. Only work sandboxes that have no `out.txt` yet.

The data format differs per task — run `head -1` first, then write your awk/script.
Do not hard-code a fixed field number.

### Terminal (50) categories
log analysis (grep/awk/sort), CSV (awk/python), JSON (jq/python), file ops (find), text (sed/awk/sort), git, archives (tar), script fixing/writing.

### TermHard (50) — harder
multi-file join/anti-join/union; state machines (max concurrent sessions); multi-stage (unpack then process); nested archives (tar in tar); git archaeology (`git log -S`, diffs between commits); encoding (base64/ROT13/hex); tricky parsing (fixed-width, quoted CSV, JSONL, INI, properties, env, Dockerfile, DNS, firewall); algorithms (sliding window, two-sum, Kadane, primes, Collatz, GCD/LCM, roman, bit-count, XOR, RPN, anagram, missing/duplicate, positive subarrays); graph (components, shortest path); multi-bug debugging.

## Common mistakes (check yourself against each)

1. **WinoGrande 0-based instead of 1-based.** Writing `0`/`1` instead of `"1"`/`"2"` → ~4% instead of ~95%. There is no `"0"` answer in WinoGrande.
2. **HumanEval written into `answers.json`** instead of `solutions.py` → 0/30.
3. **HumanEval: `Tuple`/`Any`/`Dict` in signatures, or imports at the top** → `NameError`. Import inside the function; only `List` in annotations.
4. **ARC labels are not always "A"–"D"** — some questions use numeric `"1"`–`"4"`. Take the label from the `choices` keys.
5. **Terminal: log format differs per task** — `head -1` first, then write awk. Never hard-code a fixed field number.
6. **TermHard: join without a filter** — put the condition from the task text into code.
7. **TermHard: state machine without clamp** — clamp a login/logout counter at 0.

## Helper scripts (run inside the container; paths are absolute)

- `python3 /bench/_show_batch.py <bench> <start> <n>` — show questions from index `<start>`, `<n>` of them.
- `python3 /bench/_append_batch.py <bench> '<json>'` — append a batch of answers to `/work/answers.json`. **The seven text benchmarks only. NOT HumanEval.** Re-running with the same ids updates those keys (safe for resuming an interrupted benchmark).
- `python3 /bench/_time.py <test> <start_epoch>` — write elapsed seconds to `/work/time_<test>.txt`.

## Honesty checklist (fill in at the end of your session)

- [ ] Questions were read in batches of exactly 10; each question answered once.
- [ ] My benchmark is fully done: completion check printed `OK`.
- [ ] WinoGrande (if that was your bench): every answer is `"1"` or `"2"`, no `"0"`.
- [ ] `_time.py <bench> <start_epoch>` was run as the last step.

**That is all. Scoring and comparison are not your job.**
