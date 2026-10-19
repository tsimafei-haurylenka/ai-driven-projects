import json, sys
# usage: _append_batch.py <bench> '<json dict of id->answer>'  (container)
# appends only NEW ids; ids already recorded are locked and never overwritten
bench, data = sys.argv[1], json.loads(sys.argv[2])
fp = "/work/answers.json"
try:
    A = json.load(open(fp))
except FileNotFoundError:
    A = {}
cur = A.setdefault(bench, {})
locked = sorted(k for k in data if str(k) in cur)
new = {k: v for k, v in data.items() if str(k) not in cur}
cur.update(new)
nq = len(json.load(open(f"/questions/questions_{bench}.json")))
json.dump(A, open(fp, "w"), ensure_ascii=False, indent=1)
msg = f"appended {len(new)} -> {bench} (total {len(cur)}/{nq})"
if locked:
    msg += f" | REFUSED, already recorded (do NOT re-answer): {','.join(locked)}"
print(msg)
