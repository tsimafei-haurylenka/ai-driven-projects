# Scoring & comparison (for the operator, NOT for the model)

> **This file is never shown to the model during a run.**
> The model does not read `score.py`, `compare.py`, `truth/truth_*.json`, or `results/results_*.json`.

---

## Executable protocol

Run these steps **in order**. Output: per-run scores and a comparison table.

### Run names (the core convention)

Every benchmark run has a unique name:

```
RUN_NAME = <MODEL>_<RUN_ID>          e.g. qwen3.8-27b-IQ3S_20250926-120000
```

`RUN_ID` defaults to a timestamp (`YYYYMMDD-HHMMSS`) and can be given explicitly.
**All artifacts of one run live in exactly one directory: `runs/<RUN_NAME>/`.**
Nothing is written to the bench root anymore, so:

- multiple runs of the same model on different dates never overwrite each other;
- two models (or two instances of one model) can run **in parallel** — each has its
  own stage dir and its own output files.

### Step 0. Run in isolated containers (one per category)

```bash
./run.sh <MODEL> [RUN_ID] [NOTE] [SEED]
```

`NOTE` is an optional free-text annotation (quant, context size, cache, …). It is
saved to `meta/meta_<RUN_NAME>.json` and shown in the Note column of compare.py / report.py.

`SEED` (or env `BENCH_SEED`) pins the generation RNG: pi injects `samplingParams: {"seed": N}`
into every request, so the same prompt gets identical sampling. The value is recorded in the
run's meta. Caveats: the engine must honor `seed` (llama.cpp does; check others), determinism
only holds for the same server build/params/GPU, and MTP speculative decoding plus batching
can still introduce drift — treat equal-seed reruns as *nearly* identical, not bit-exact.

**One fresh Docker container per benchmark** (10 launches total), started by run.sh from
the host. Each launch mounts only its own category's questions (+ clean task sandboxes for
terminal benches) plus `runs/<RUN_NAME>/` as `/work`, so answers accumulate across launches:
each pi session starts with a clean context and never compacts mid-run (that is the point of
this design — check the event logs below if in doubt). `truth/truth_*.json`, other runs' answers,
and scoring scripts do not exist inside. Live progress is printed to the console; raw event
logs are saved per bench at `runs/<RUN_NAME>/pi_stream_<bench>.jsonl`.

Environment overrides (match your LM Studio setup):
- `BENCH_LMSTUDIO_HOST` / `BENCH_LMSTUDIO_PORT` — endpoint, default `host.docker.internal:1234`
  (the port is also checked from the host before any container starts);
- `BENCH_CTX` — context window written into pi's models.json; set it to LM Studio's actual
  window size so compaction thresholds stay honest;
- `BENCH_LMSTUDIO_KEY` — API key if your server requires one (default: placeholder, ignored).

If a run is interrupted, partial terminal outputs can still be salvaged from
`.stage/<RUN_NAME>/<bench>/tasks/.../out.txt` (the stage dir is deleted only on full success),
or re-run just the missing benches with `RESUME=1 ./run.sh <MODEL> <same RUN_ID>` — completed
benches are skipped, incomplete ones **continue where they stopped**: already-recorded answers
are locked and hidden from the model, sandbox tasks that already have `out.txt` are restored
into the new stage and skipped, functions already in `solutions.py` are locked. Each retry of
a bench keeps the previous attempt's event log as `pi_stream_<bench>.attemptN.jsonl`.

`INSTRUCTIONS.md` is mounted from the bench root into every container at launch,
so editing it needs no `docker build`; only Dockerfile changes (helpers, pi version) do.

### Step 1. Quick verification (host-side)

```bash
python3 verify.py <RUN_NAME>
```

Compares `runs/<RUN_NAME>/answers.json` against `truth/truth_*.json` and prints accuracy
per benchmark. Host-only — never runs in the container.

### Step 2. Check that all answer files are present

```bash
ls runs/<RUN_NAME>/answers.json runs/<RUN_NAME>/solutions.py
ls runs/<RUN_NAME>/terminal/ runs/<RUN_NAME>/terminal_hard/
```

A complete run has:
- `runs/<RUN_NAME>/answers.json` (text benchmarks 1–7);
- `runs/<RUN_NAME>/solutions.py` (HumanEval);
- `runs/<RUN_NAME>/terminal/<task>.txt` and `runs/<RUN_NAME>/terminal_hard/<task>.txt`
  (one per task — only if the model did those benchmarks).

### Step 3. Score the run

```bash
./.benchenv/bin/python score.py <RUN_NAME>
```

Reads everything from `runs/<RUN_NAME>/` and writes `results/results_<RUN_NAME>.json`
(per-benchmark scores + time). Run order does not matter: each run is independent.

### Step 4. Run metadata (recorded automatically)

`run.sh` records everything needed into `meta/meta_<RUN_NAME>.json` — no manual step:
- `notes` — the 3rd launch argument (quant, cache, …);
- `context_size` — the `BENCH_CTX` this run used (set the env to the model's real
  window at launch so this value is honest);
- `compactions` — counted automatically from the run's event logs
  (`compaction_start` events across all attempts of all benches).

With the per-category design each session is short, so expect **0** compactions; a
non-zero count means some session grew too long and its scores are less comparable.
Only correct a value manually (edit the JSON) if it was recorded wrong — e.g. the
launch used the default `BENCH_CTX` but the model's real window differed.
`report.py` prints these values per run; without a meta file it shows `-`, and the
comparison is incomplete (equal scores with different compaction counts are not
comparable).

### Step 5. Print the comparison table

```bash
./.benchenv/bin/python compare.py
```

One row per `results/results_*.json` — i.e. one row **per run**, not per model.

### Step 6. Print the full report (per benchmark + time + context)

```bash
python3 report.py
```

Prints:
1. Summary table: run × benchmark (acc %).
2. Per-run detail: correct/total, acc, **time per test**, total time.
3. Context size and compaction count from `meta/meta_<RUN_NAME>.json`.

> **Time format:** everywhere (`compare.py`, `report.py`, `score.py`) time is
> printed in **seconds only** — no rounding to minutes/hours, no `h`/`m` suffixes.
> Always `N.Ns`.

### Keeping the table honest / starting from scratch

`clean.py` wipes runs so `compare.py`/`report.py` start over:

```bash
python3 clean.py --list                  # all runs with [scored]/[raw] status
python3 clean.py <RUN_NAME> ...          # move one run's artifacts to .prev_runs/ (restorable)
python3 clean.py --all                   # archive every run
python3 clean.py --all --purge --yes     # delete outright (junk runs, methodology change, fresh start)
```

It touches only `runs/`, `results/`, `meta/`, `.stage/` — questions and truth are
never removed. Archive mode is the default; `--purge` deletes for good.
Typical use after a methodology change (e.g. adding a generation seed):
purge the incomparable old runs, then re-run the whole set with one seed, e.g.
`BENCH_SEED=1234 ./run.sh <MODEL> <same-RUN_ID-per-model> ...` so every row shares a seed.
- Never hand-edit `answers.json` / `solutions.py` after scoring.

**Done.** Steps 5 and 6 together give the full comparison across all runs.

---

## Reference (how scoring works)

### Per-benchmark method in score.py

| Benchmark | Comparison |
|---|---|
| MMLU, HellaSwag, ARC, WinoGrande | `norm_str(answer) == norm_str(truth)` — string compare after normalization |
| GSM8K, MATH | `to_number(answer) ≈ to_number(truth)` within 1e-6; understands fractions "a/b", `\frac{a}{b}`, `\pi`, `\sqrt{}` |
| BBH | `norm_str(answer) == norm_str(truth)` ("yes"/"no" case-insensitive) |
| HumanEval | unit tests (see below) |
| Terminal, TermHard | reads `runs/<RUN_NAME>/terminal[_hard]/<task>.txt`, compares with truth (numbers 1e-6, strings normalized) |

### Answer format per benchmark (what the model writes into answers.json)

`answers.json` is one JSON object: `{ "<bench>": { "<question_id>": <answer> } }`.
Bench keys: `mmlu, gsm8k, arc, hellaswag, math, winogrande, bbh`. (A stray
`humaneval` key with placeholder values is ignored — HumanEval lives in
`solutions.py`, not in answers.json.)

| Bench | Answer format | Example |
|---|---|---|
| mmlu | int 0–3 (0-based option index) | `2` |
| gsm8k | string, final number only | `"42"` |
| arc | string, the option **label** from that question's `choices` keys ("A"–"D" or "1"–"4") | `"B"` |
| hellaswag | int 0–3 (0-based) | `1` |
| math | string: number, fraction "a/b", or expression | `"3/4"`, `"2*sqrt(2)"` |
| winogrande | string **"1" or "2"** — 1-based! Never "0". | `"2"` |
| bbh | string "Yes"/"No" (case-insensitive) | `"yes"` |

### HumanEval harness (critical)

For each function:
1. Only `inspect.getsource(fn)` from `solutions.py` is taken.
2. It is placed in a temp file with exactly one import: `from typing import List`.
3. The unit test from `truth/truth_humaneval.json` is appended and run with a 30 s timeout.

Consequences:
- Imports at the top of `solutions.py` do NOT reach the temp file — needed imports
  go **inside the function body**.
- `Tuple`, `Any`, `Dict` in signatures → `NameError`.
- If the prompt provides a helper (e.g. `encode_cyclic` in HumanEval/38), it is
  NOT included in the temp file.

**Known broken test: HumanEval/38 (`decode_cyclic`)** — its unit test calls
`encode_cyclic` at module level, which the harness never includes. The test is
unpassable by any correct solution. Achievable maximum: **29/30**.

### Terminal / TermHard scoring

- `score.py` reads `runs/<RUN_NAME>/terminal/<task>.txt` (or `terminal_hard/`).
- Numbers compared within 1e-6; strings by normalization.
- The **actual artifact** is verified, not the model's self-report.

### Run metadata: `meta/meta_<RUN_NAME>.json`

- `context_size` — inference context window in tokens.
- `compactions` — number of context compactions during the run.
- `notes` — free text (inference version, quantization, cache, …).

Compaction affects fairness (the model may lose task conditions/formats from
context), so it is recorded per run. Without these values the comparison is
**incomplete**.

## Errors observed in past runs (do not repeat)

1. **WinoGrande: 0-based instead of 1-based** → score 2.5% instead of ~95%.
   Format: "1" or "2", never "0".
2. **Incomplete benchmark** (e.g. ARC: 100 of 200) → deflated score.
   Protection: self-check the count before scoring; an aborted run shows 0 for every
   benchmark after the stop point — archive it, do not read it as a real result.
3. **HumanEval: NameError from `Tuple`/`Any` in signatures, `hashlib` at file top**
   → extra failures. Imports inside the function; only `List` in annotations.
4. **HumanEval/38 is unpassable by design** (test requires `encode_cyclic`).
   Expected maximum: 29/30.
5. **ARC: labels are not always "A"–"D"** — some questions use numeric "1"–"4".
   Take the label from the `choices` keys.
6. **Terminal: log format DIFFERS per task** — `head -1` first, then write awk.
   Never hard-code a field number.
7. **TermHard: join without filter** — the condition from the task text must be in the code.
8. **TermHard: state machine without clamp** — clamp the login/logout counter at 0.

## Limitations / honest caveats

- Finite samples (100–200 per benchmark vs 1000+ in the originals) → variance.
- Code is `pass@1`: one attempt, no iteration against tests.
- MATH: answers are normalized (numbers, `\frac{}` fractions), but exotic
  expressions may be compared as strings.
- Different models may interpret "option index" differently — strictly follow the
  format in INSTRUCTIONS.md.
