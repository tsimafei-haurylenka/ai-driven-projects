import json, re, sys
# usage: _show_batch.py <bench> <start> <n>  (container)
# window over the FULL list (start:n stable), prints only questions with no recorded answer
bench, start, n = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
qs = json.load(open(f"/questions/questions_{bench}.json"))
answered = set()
if bench == "humaneval":
    try:
        sol = open("/work/solutions.py").read()
        for q in qs:
            m = re.search(r"def (\w+)\(", q["prompt"])
            if m and re.search(rf"def {m.group(1)}\(", sol):
                answered.add(str(q["id"]))
    except FileNotFoundError:
        pass
else:
    try:
        answered = {str(k) for k in json.load(open("/work/answers.json")).get(bench, {})}
    except FileNotFoundError:
        answered = set()
window = qs[start:start + n]
shown = [q for q in window if str(q["id"]) not in answered]
print(f"# {bench}: answered {len(answered)}/{len(qs)}, remaining {len(qs) - len(answered)}; window [{start}:{start + n}] shows {len(shown)} (locked ones hidden — move to the next window)")
for q in shown:
    print(json.dumps(q, ensure_ascii=False))
