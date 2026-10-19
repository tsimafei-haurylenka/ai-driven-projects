import json, re, sys
# usage: _todo.py <bench>  (container) -> answered/remaining status (locked = done)
bench = sys.argv[1]
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
todo = len(qs) - len(answered)
# always exit 0: a non-zero code renders as "bash failed" in the harness;
# the model reads the printed status, not the exit code
print(f"{bench}: answered {len(answered)}/{len(qs)}, remaining {todo}")
