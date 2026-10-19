"""PHASE 1 (terminal-hard) — 50 HARDER terminal-agent tasks.

Same protocol as prepare_terminal.py but tasks require multi-step pipelines,
multi-file joins, state machines, git archaeology, nested archives,
algorithmic computation, tricky parsing, and multi-bug debugging.

Run:  ./.benchenv/bin/python bench/prepare_terminal_hard.py
"""
import json, os, random, hashlib, shutil, collections, zlib, subprocess, base64, codecs

OUT = os.path.dirname(os.path.abspath(__file__))
SB = os.path.join(OUT, "terminal_sandboxes_hard")
SEED = 1234
N_TASKS = 50

def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)

def wb(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)

def rnd(rng, a, b):
    return rng.randint(a, b)

def pick(rng, seq):
    return seq[rng.randrange(len(seq))]

TASKS = []
def task(name):
    def deco(fn):
        TASKS.append((name, fn))
        return fn
    return deco

# ---- 1. Multi-file join: sum amount for orders whose customer is VIP ----
@task("join_vip_revenue")
def t_join_vip(sandbox, rng):
    n_cust = 50
    customers = {}
    for i in range(n_cust):
        customers[f"c{i}"] = "VIP" if rng.random() < 0.3 else "REG"
    w(os.path.join(sandbox, "customers.csv"), "id,tier\n" + "\n".join(f"{k},{v}" for k, v in customers.items()) + "\n")
    tot = 0
    rows = []
    for i in range(300):
        cid = f"c{rnd(rng,0,n_cust-1)}"
        amt = rnd(rng, 1, 500)
        rows.append(f"{i},{cid},{amt}")
        if customers[cid] == "VIP":
            tot += amt
    w(os.path.join(sandbox, "orders.csv"), "id,customer_id,amount\n" + "\n".join(rows) + "\n")
    return ("Two CSV files: customers.csv (id,tier) and orders.csv (id,customer_id,amount).\n"
            "Sum the 'amount' over all orders whose customer_id has tier == 'VIP' in customers.csv.\n"
            "Write the total (a single integer) to out.txt.\n", str(tot))

# ---- 2. Anti-join: products with no orders ----
@task("anti_join_products")
def t_anti_join(sandbox, rng):
    prods = [f"p{i}" for i in range(30)]
    order_list = [pick(rng, prods) for _ in range(80)]
    w(os.path.join(sandbox, "products.csv"), "\n".join(prods) + "\n")
    w(os.path.join(sandbox, "orders.csv"), "\n".join(order_list) + "\n")
    n = len(set(prods) - set(order_list))
    return ("products.csv has one product id per line (no header). orders.csv has one product_id\n"
            "per line (no header). Count how many products have NO orders.\n"
            "Write that count to out.txt.\n", str(n))

# ---- 3. Full outer join: count distinct keys across two files ----
@task("full_outer_keys")
def t_full_outer(sandbox, rng):
    keys_a = set(f"k{rnd(rng,0,40)}" for _ in range(30))
    keys_b = set(f"k{rnd(rng,0,40)}" for _ in range(30))
    w(os.path.join(sandbox, "a.txt"), "\n".join(sorted(keys_a)) + "\n")
    w(os.path.join(sandbox, "b.txt"), "\n".join(sorted(keys_b)) + "\n")
    n = len(keys_a | keys_b)
    return ("a.txt and b.txt each contain a list of keys (one per line).\n"
            "Count the number of DISTINCT keys that appear in EITHER file (union).\n"
            "Write that count to out.txt.\n", str(n))

# ---- 4. State machine: max concurrent sessions ----
@task("max_concurrency")
def t_max_conc(sandbox, rng):
    events = []
    for _ in range(200):
        events.append((rnd(rng, 1, 1000), pick(rng, ["login", "logout"])))
    events.sort()
    cur = 0; mx = 0
    for _, e in events:
        if e == "login":
            cur += 1; mx = max(mx, cur)
        else:
            cur = max(0, cur - 1)
    w(os.path.join(sandbox, "events.log"), "\n".join(f"{t} {e}" for t, e in events) + "\n")
    return ("events.log has lines '<timestamp> <event>' where event is 'login' or 'logout'.\n"
            "Events are already sorted by timestamp. A 'login' starts a session, a 'logout' ends one.\n"
            "Find the MAXIMUM number of sessions that were active at the same time.\n"
            "Write that number to out.txt.\n", str(mx))

# ---- 5. Multi-stage: extract tar, then count ----
@task("tar_then_count")
def t_tar_then_count(sandbox, rng):
    import tarfile
    # inner: files with a marker
    inner_dir = os.path.join(sandbox, "_inner")
    os.makedirs(inner_dir, exist_ok=True)
    n_marker = 0
    for f in range(20):
        content = f"line1\nline2\n"
        if rng.random() < 0.4:
            content += "SECRET\n"
            n_marker += 1
        w(os.path.join(inner_dir, f"f{f}.txt"), content)
    with tarfile.open(os.path.join(sandbox, "stage1.tar"), "w") as tf:
        tf.add(inner_dir)
    shutil.rmtree(inner_dir)
    return ("stage1.tar contains a directory with .txt files. Extract it, then count how many\n"
            "files contain the word 'SECRET'. Write that count to out.txt.\n", str(n_marker))

# ---- 6. Nested archive: tar inside tar ----
@task("nested_tar")
def t_nested_tar(sandbox, rng):
    import tarfile
    inner_dir = os.path.join(sandbox, "_inner")
    os.makedirs(inner_dir, exist_ok=True)
    n = 0
    for f in range(15):
        w(os.path.join(inner_dir, f"d{f}.dat"), "x\n")
        n += 1
    inner_tar = os.path.join(sandbox, "_inner.tar")
    with tarfile.open(inner_tar, "w") as tf:
        tf.add(inner_dir)
    shutil.rmtree(inner_dir)
    outer_dir = os.path.join(sandbox, "_outer")
    os.makedirs(outer_dir, exist_ok=True)
    shutil.move(inner_tar, os.path.join(outer_dir, "inner.tar"))
    outer_tar = os.path.join(sandbox, "archive.tar")
    with tarfile.open(outer_tar, "w") as tf:
        tf.add(outer_dir)
    shutil.rmtree(outer_dir)
    return ("archive.tar contains a directory with a file inner.tar inside it.\n"
            "inner.tar contains .dat files. Count the total number of .dat files inside inner.tar.\n"
            "Write that count to out.txt.\n", str(n))

# ---- 7. Git: find commit that introduced a string ----
@task("git_introduced")
def t_git_introduced(sandbox, rng):
    repo = os.path.join(sandbox, "repo")
    os.makedirs(repo, exist_ok=True)
    env = dict(os.environ, GIT_AUTHOR_NAME="dev", GIT_AUTHOR_EMAIL="d@x.com",
               GIT_COMMITTER_NAME="dev", GIT_COMMITTER_EMAIL="d@x.com",
               GIT_AUTHOR_DATE="2023-01-01T00:00:00+0000",
               GIT_COMMITTER_DATE="2023-01-01T00:00:00+0000")
    def git(*a):
        subprocess.run(["git", *a], cwd=repo, env=env, check=True, capture_output=True)
    git("init", "-q")
    n_before = rnd(rng, 2, 5)
    for i in range(n_before):
        w(os.path.join(repo, "f.txt"), f"v{i}\n")
        git("add", "."); git("commit", "-q", "-m", f"before {i}")
    target = f"magic-{rnd(rng,1000,9999)}"
    w(os.path.join(repo, "f.txt"), f"v{n_before}\n{target}\n")
    git("add", "."); git("commit", "-q", "-m", "introduce")
    for i in range(rnd(rng, 1, 3)):
        w(os.path.join(repo, "f.txt"), f"v{n_before+i+1}\n{target}\n")
        git("add", "."); git("commit", "-q", "-m", f"after {i}")
    # find the commit hash that first introduced target
    r = subprocess.run(["git", "log", "-S", target, "--format=%H", "HEAD"],
                       cwd=repo, capture_output=True, text=True)
    hashes = r.stdout.strip().split("\n")
    first = hashes[-1]  # oldest
    return ("A git repository is at 'repo'. A specific string was introduced in one commit.\n"
            f"Find the commit that FIRST introduced the string '{target}'.\n"
            "Write that commit's full hash to out.txt.\n", first)

# ---- 8. Git: lines changed per author ----
@task("git_lines_per_author")
def t_git_lines_author(sandbox, rng):
    repo = os.path.join(sandbox, "repo")
    os.makedirs(repo, exist_ok=True)
    def git(*a, name="alice"):
        env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=f"{name}@x.com",
                   GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=f"{name}@x.com")
        subprocess.run(["git", *a], cwd=repo, env=env, check=True, capture_output=True)
    git("init", "-q")
    w(os.path.join(repo, "f.txt"), "start\n")
    git("add", "."); git("commit", "-q", "-m", "init", name="root")
    alice_lines = 0
    for i in range(rnd(rng, 2, 5)):
        nlines = rnd(rng, 3, 10)
        alice_lines += nlines
        w(os.path.join(repo, "f.txt"), "\n".join(f"a{i}_{j}" for j in range(nlines)) + "\n")
        git("add", "."); git("commit", "-q", "-m", f"alice {i}", name="alice")
    for i in range(rnd(rng, 2, 5)):
        nlines = rnd(rng, 3, 10)
        w(os.path.join(repo, "f.txt"), "\n".join(f"b{i}_{j}" for j in range(nlines)) + "\n")
        git("add", "."); git("commit", "-q", "-m", f"bob {i}", name="bob")
    return ("A git repository is at 'repo' with commits by 'alice' and 'bob'.\n"
            "Count the total number of lines ADDED by 'alice' across all her commits.\n"
            "Write that count to out.txt.\n", str(alice_lines))

# ---- 9. Git: diff between two commits ----
@task("git_diff_commits")
def t_git_diff(sandbox, rng):
    repo = os.path.join(sandbox, "repo")
    os.makedirs(repo, exist_ok=True)
    env = dict(os.environ, GIT_AUTHOR_NAME="dev", GIT_AUTHOR_EMAIL="d@x.com",
               GIT_COMMITTER_NAME="dev", GIT_COMMITTER_EMAIL="d@x.com")
    def git(*a):
        subprocess.run(["git", *a], cwd=repo, env=env, check=True, capture_output=True)
    git("init", "-q")
    w(os.path.join(repo, "f.txt"), "one\ntwo\nthree\n")
    git("add", "."); git("commit", "-q", "-m", "c0")
    h1 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    nlines = rnd(rng, 5, 15)
    w(os.path.join(repo, "f.txt"), "\n".join(f"line{i}" for i in range(nlines)) + "\n")
    git("add", "."); git("commit", "-q", "-m", "c1")
    h2 = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True).stdout.strip()
    w(os.path.join(sandbox, "commits.txt"), f"{h1}\n{h2}\n")
    return ("A git repository is at 'repo'. commits.txt has two commit hashes (older first).\n"
            "Count how many lines were ADDED in the diff between those two commits.\n"
            "Write that count to out.txt.\n", str(nlines))

# ---- 10. Base64 decode then count ----
@task("base64_decode_count")
def t_b64(sandbox, rng):
    words = [f"w{rnd(rng,0,30)}" for _ in range(100)]
    text = " ".join(words)
    encoded = base64.b64encode(text.encode()).decode()
    w(os.path.join(sandbox, "data.b64"), encoded + "\n")
    n_distinct = len(set(words))
    return ("data.b64 contains a base64-encoded string. Decode it. The decoded text is a\n"
            "space-separated list of words. Count how many DISTINCT words there are.\n"
            "Write that count to out.txt.\n", str(n_distinct))

# ---- 11. ROT13 decode then find ----
@task("rot13_find")
def t_rot13(sandbox, rng):
    secret = f"key-{rnd(rng,1000,9999)}"
    lines = []
    for _ in range(50):
        if rng.random() < 0.2:
            lines.append(codecs.encode(secret, "rot_13"))
        else:
            lines.append(codecs.encode(f"noise{rnd(rng,0,99)}", "rot_13"))
    w(os.path.join(sandbox, "encoded.txt"), "\n".join(lines) + "\n")
    return ("encoded.txt has lines that are ROT13-encoded. Decode each line (ROT13).\n"
            f"Find the line that, when decoded, starts with 'key-'. Write that decoded line to out.txt.\n",
            secret)

# ---- 12. Hex decode ----
@task("hex_decode")
def t_hex(sandbox, rng):
    msg = f"msg-{rnd(rng,10000,99999)}"
    hexstr = msg.encode().hex()
    w(os.path.join(sandbox, "data.hex"), hexstr + "\n")
    return ("data.hex contains a hexadecimal string. Decode it to ASCII.\n"
            "Write the decoded string to out.txt.\n", msg)

# ---- 13. Fixed-width parsing ----
@task("fixed_width")
def t_fixed_width(sandbox, rng):
    rows = []
    tot = 0
    for i in range(200):
        name = f"n{i:03d}"[:8].ljust(8)
        age = rnd(rng, 18, 70)
        sal = rnd(rng, 1000, 9999)
        rows.append(f"{name}{age:4d}{sal:6d}")
        if age > 40:
            tot += sal
    w(os.path.join(sandbox, "data.txt"), "\n".join(rows) + "\n")
    return ("data.txt has fixed-width records: columns are name(8), age(4), salary(6).\n"
            "Sum 'salary' over all rows where age > 40. Write the total to out.txt.\n", str(tot))

# ---- 14. Quoted CSV (commas inside quotes) ----
@task("quoted_csv")
def t_quoted_csv(sandbox, rng):
    rows = []
    tot = 0
    for i in range(100):
        # description may contain commas
        desc = f"item {rnd(rng,0,20)}, variant {rnd(rng,0,5)}" if rng.random() < 0.5 else f"item {rnd(rng,0,20)}"
        price = rnd(rng, 1, 100)
        rows.append(f'{i},"{desc}",{price}')
        if "," in desc:
            tot += price
    w(os.path.join(sandbox, "items.csv"), "id,description,price\n" + "\n".join(rows) + "\n")
    return ("items.csv columns: id,description,price. The description field is quoted and MAY\n"
            "contain commas. Sum 'price' over rows where the description contains a comma.\n"
            "Write the total to out.txt.\n", str(tot))

# ---- 15. JSONL (line-delimited JSON) ----
@task("jsonl_sum")
def t_jsonl(sandbox, rng):
    lines = []
    tot = 0
    for i in range(200):
        val = rnd(rng, 1, 100)
        lines.append(json.dumps({"id": i, "val": val}))
        if val > 50:
            tot += val
    w(os.path.join(sandbox, "data.jsonl"), "\n".join(lines) + "\n")
    return ("data.jsonl is line-delimited JSON (one JSON object per line), each with id and val.\n"
            "Sum 'val' over all lines where val > 50. Write the total to out.txt.\n", str(tot))

# ---- 16. INI file parsing ----
@task("ini_sum")
def t_ini(sandbox, rng):
    sections = [f"sec{i}" for i in range(5)]
    lines = []
    tot = 0
    target = pick(rng, sections)
    for s in sections:
        lines.append(f"[{s}]")
        for k in range(rnd(rng, 2, 6)):
            v = rnd(rng, 1, 100)
            lines.append(f"key{k} = {v}")
            if s == target:
                tot += v
    w(os.path.join(sandbox, "config.ini"), "\n".join(lines) + "\n")
    return (f"config.ini is an INI file with sections and 'key = value' lines.\n"
            f"Sum all the integer values in the section [{target}]. Write the total to out.txt.\n",
            str(tot))

# ---- 17. Properties file: count keys matching pattern ----
@task("props_count")
def t_props(sandbox, rng):
    lines = []
    n = 0
    for i in range(100):
        if rng.random() < 0.3:
            lines.append(f"db.connection.{i} = pool{i}")
            n += 1
        else:
            lines.append(f"app.setting.{i} = val{i}")
    w(os.path.join(sandbox, "app.properties"), "\n".join(lines) + "\n")
    return ("app.properties has lines 'key = value'. Count lines where the key starts with 'db.connection'.\n"
            "Write that count to out.txt.\n", str(n))

# ---- 18. Env file: count exported vars ----
@task("env_count")
def t_env(sandbox, rng):
    lines = []
    n = 0
    for i in range(80):
        if rng.random() < 0.4:
            lines.append(f"export VAR_{i}={rnd(rng,0,999)}")
            n += 1
        else:
            lines.append(f"# comment {i}")
    w(os.path.join(sandbox, ".env"), "\n".join(lines) + "\n")
    return (".env has lines. Some are 'export NAME=value', some are comments (#).\n"
            "Count the lines that start with 'export '. Write that count to out.txt.\n", str(n))

# ---- 19. Dockerfile: count layers ----
@task("dockerfile_layers")
def t_docker(sandbox, rng):
    instrs = ["RUN", "RUN", "COPY", "ADD", "ENV", "CMD", "RUN", "COPY"]
    lines = ["FROM ubuntu:22.04"]
    n_run = 0
    for _ in range(30):
        ins = pick(rng, instrs)
        lines.append(f"{ins} /something")
        if ins == "RUN":
            n_run += 1
    w(os.path.join(sandbox, "Dockerfile"), "\n".join(lines) + "\n")
    return ("Dockerfile has instructions. Count how many RUN instructions there are.\n"
            "Write that count to out.txt.\n", str(n_run))

# ---- 20. URL parsing: count by domain ----
@task("url_domain")
def t_url(sandbox, rng):
    domains = [f"d{i}.com" for i in range(10)]
    lines = []
    for _ in range(200):
        d = pick(rng, domains)
        lines.append(f"https://{d}/path{rnd(rng,0,50)}?q={rnd(rng,0,9)}")
    w(os.path.join(sandbox, "urls.txt"), "\n".join(lines) + "\n")
    c = collections.Counter(l.split("/")[2] for l in lines)
    top = c.most_common(1)[0][0]
    return ("urls.txt has one URL per line. Find the domain (host) that appears MOST often.\n"
            "Write that domain to out.txt.\n", top)

# ---- 21. DNS zone: count records by type ----
@task("dns_zone")
def t_dns(sandbox, rng):
    types = ["A", "A", "A", "AAAA", "CNAME", "MX"]
    lines = []
    for _ in range(200):
        t = pick(rng, types)
        lines.append(f"name{rnd(rng,0,30)}  60  IN  {t}  data")
    w(os.path.join(sandbox, "zone.txt"), "\n".join(lines) + "\n")
    c = collections.Counter(l.split()[3] for l in lines)
    top = c.most_common(1)[0][0]
    return ("zone.txt has DNS records: '<name> <ttl> IN <type> <data>'.\n"
            "Find the record type (4th field) that appears MOST often. Write it to out.txt.\n", top)

# ---- 22. Firewall log: count blocked unique IPs ----
@task("fw_blocked")
def t_fw(sandbox, rng):
    ips = [f"10.{rnd(rng,0,9)}.{rnd(rng,0,9)}.{rnd(rng,1,255)}" for _ in range(40)]
    lines = []
    blocked = set()
    for _ in range(300):
        ip = pick(rng, ips)
        action = pick(rng, ["ALLOW", "ALLOW", "BLOCK"])
        lines.append(f'{ip} {action} port {rnd(rng,1,65535)}')
        if action == "BLOCK":
            blocked.add(ip)
    w(os.path.join(sandbox, "fw.log"), "\n".join(lines) + "\n")
    return ("fw.log has lines '<ip> <action> port <port>'.\n"
            "Count how many DISTINCT IPs were BLOCKED. Write that count to out.txt.\n", str(len(blocked)))

# ---- 23. Word frequency with tie-break ----
@task("word_tiebreak")
def t_word_tie(sandbox, rng):
    pool = [f"w{i}" for i in range(20)]
    words = [pick(rng, pool) for _ in range(500)]
    w(os.path.join(sandbox, "corpus.txt"), " ".join(words) + "\n")
    c = collections.Counter(words)
    # most common; tie-break alphabetically
    top = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    return ("corpus.txt is a line of space-separated words. Find the most frequent word.\n"
            "If there is a tie, pick the one that comes FIRST alphabetically.\n"
            "Write that word to out.txt.\n", top)

# ---- 24. Longest line ----
@task("longest_line")
def t_longest(sandbox, rng):
    lines = [f"line{i} " + "x" * rnd(rng, 1, 200) for i in range(100)]
    w(os.path.join(sandbox, "lines.txt"), "\n".join(lines) + "\n")
    mx = max(len(l) for l in lines)
    return ("lines.txt has many lines of varying length. Find the length (in characters) of the\n"
            "LONGEST line. Write that number to out.txt.\n", str(mx))

# ---- 25. Intersection of two files ----
@task("file_intersection")
def t_intersect(sandbox, rng):
    pool = [f"v{rnd(rng,0,50)}" for _ in range(100)]
    a = set(pool[:50]); b = set(pool[50:])
    # ensure some overlap
    for i in range(10):
        b.add(pool[i])
    w(os.path.join(sandbox, "a.txt"), "\n".join(sorted(a)) + "\n")
    w(os.path.join(sandbox, "b.txt"), "\n".join(sorted(b)) + "\n")
    n = len(a & b)
    return ("a.txt and b.txt each have a list of values (one per line).\n"
            "Count how many values appear in BOTH files (intersection). Write that count to out.txt.\n",
            str(n))

# ---- 26. Symmetric difference ----
@task("sym_diff")
def t_symdiff(sandbox, rng):
    pool = [f"v{rnd(rng,0,50)}" for _ in range(100)]
    a = set(pool[:60]); b = set(pool[40:])
    w(os.path.join(sandbox, "a.txt"), "\n".join(sorted(a)) + "\n")
    w(os.path.join(sandbox, "b.txt"), "\n".join(sorted(b)) + "\n")
    n = len(a ^ b)
    return ("a.txt and b.txt each have a list of values (one per line).\n"
            "Count how many values appear in EXACTLY ONE of the two files (symmetric difference).\n"
            "Write that count to out.txt.\n", str(n))

# ---- 27. Natural sort: k-th filename ----
@task("natural_sort")
def t_natural(sandbox, rng):
    names = [f"file{n:03d}.txt" for n in [3, 10, 25, 100, 42, 7, 999, 1, 500, 200]]
    rng.shuffle(names)
    base = os.path.join(sandbox, "files")
    os.makedirs(base, exist_ok=True)
    for n in names:
        w(os.path.join(base, n), "x\n")
    # natural sort: file1 < file3 < file7 < file10 < file25 < file42 < file100 < file200 < file500 < file999
    sorted_names = sorted(names, key=lambda s: int("".join(filter(str.isdigit, s))))
    kth = sorted_names[4]  # 5th in natural order
    return ("In the directory 'files' there are files named file<N>.txt.\n"
            "Sort them by NATURAL order (by the number N, not lexicographically).\n"
            "Write the 5th file in that sorted order (just the filename) to out.txt.\n", kth)

# ---- 28. Version sort: highest version ----
@task("version_sort")
def t_version(sandbox, rng):
    versions = []
    for _ in range(20):
        versions.append(f"{rnd(rng,0,5)}.{rnd(rng,0,20)}.{rnd(rng,0,30)}")
    w(os.path.join(sandbox, "versions.txt"), "\n".join(versions) + "\n")
    def vkey(v):
        return tuple(int(x) for x in v.split("."))
    top = max(versions, key=vkey)
    return ("versions.txt has semantic versions (X.Y.Z) one per line.\n"
            "Find the HIGHEST version (compare X, then Y, then Z numerically). Write it to out.txt.\n",
            top)

# ---- 29. Date: latest event ----
@task("date_latest")
def t_date_latest(sandbox, rng):
    dates = [f"2023-{rnd(rng,1,12):02d}-{rnd(rng,1,28):02d}" for _ in range(100)]
    w(os.path.join(sandbox, "events.txt"), "\n".join(dates) + "\n")
    top = max(dates)
    return ("events.txt has dates in YYYY-MM-DD format, one per line.\n"
            "Find the LATEST (most recent) date. Write it to out.txt.\n", top)

# ---- 30. Merge two sorted files ----
@task("merge_sorted")
def t_merge(sandbox, rng):
    a = sorted(rnd(rng, 1, 100) for _ in range(50))
    b = sorted(rnd(rng, 1, 100) for _ in range(50))
    w(os.path.join(sandbox, "a.txt"), "\n".join(map(str, a)) + "\n")
    w(os.path.join(sandbox, "b.txt"), "\n".join(map(str, b)) + "\n")
    merged = sorted(a + b)
    kth = merged[49]  # 50th smallest
    return ("a.txt and b.txt each contain sorted integers (one per line).\n"
            "Merge them into one sorted list. Write the 50th smallest value to out.txt.\n", str(kth))

# ---- 31. Chunk: sum of every k-th ----
@task("every_kth")
def t_every_kth(sandbox, rng):
    vals = [rnd(rng, 1, 100) for _ in range(100)]
    w(os.path.join(sandbox, "nums.txt"), "\n".join(map(str, vals)) + "\n")
    tot = sum(vals[::7])  # every 7th, starting from index 0
    return ("nums.txt has one integer per line (100 lines, 0-indexed).\n"
            "Sum the values at indices 0, 7, 14, 21, ... (every 7th line, starting from the first).\n"
            "Write the total to out.txt.\n", str(tot))

# ---- 32. Sliding window: max sum of k consecutive ----
@task("sliding_window")
def t_sliding(sandbox, rng):
    vals = [rnd(rng, 1, 50) for _ in range(100)]
    w(os.path.join(sandbox, "nums.txt"), "\n".join(map(str, vals)) + "\n")
    k = 10
    mx = max(sum(vals[i:i+k]) for i in range(len(vals) - k + 1))
    return ("nums.txt has one integer per line. Find the maximum sum of any 10 CONSECUTIVE values.\n"
            "Write that maximum sum to out.txt.\n", str(mx))

# ---- 33. Two-sum: count pairs ----
@task("two_sum")
def t_two_sum(sandbox, rng):
    vals = [rnd(rng, 1, 50) for _ in range(60)]
    w(os.path.join(sandbox, "nums.txt"), "\n".join(map(str, vals)) + "\n")
    target = 30
    c = 0
    for i in range(len(vals)):
        for j in range(i+1, len(vals)):
            if vals[i] + vals[j] == target:
                c += 1
    return (f"nums.txt has one integer per line. Count the number of PAIRS (i<j) where\n"
            f"nums[i] + nums[j] == {target}. Write that count to out.txt.\n", str(c))

# ---- 34. Kadane: max subarray ----
@task("max_subarray")
def t_kadane(sandbox, rng):
    vals = [rnd(rng, -10, 10) for _ in range(100)]
    w(os.path.join(sandbox, "nums.txt"), "\n".join(map(str, vals)) + "\n")
    best = cur = vals[0]
    for v in vals[1:]:
        cur = max(v, cur + v)
        best = max(best, cur)
    return ("nums.txt has one integer per line (can be negative). Find the maximum sum of any\n"
            "CONTIGUOUS subarray. Write that maximum sum to out.txt.\n", str(best))

# ---- 35. Primes: count in range ----
@task("primes_count")
def t_primes(sandbox, rng):
    lo, hi = rnd(rng, 1, 500), rnd(rng, 501, 1000)
    if lo > hi: lo, hi = hi, lo
    def is_prime(n):
        if n < 2: return False
        for i in range(2, int(n**0.5)+1):
            if n % i == 0: return False
        return True
    n = sum(1 for x in range(lo, hi+1) if is_prime(x))
    w(os.path.join(sandbox, "range.txt"), f"{lo}\n{hi}\n")
    return (f"range.txt has two lines: a lower bound and an upper bound.\n"
            f"Count how many PRIME numbers are in the inclusive range [lo, hi]. Write that count to out.txt.\n",
            str(n))

# ---- 36. Collatz steps ----
@task("collatz")
def t_collatz(sandbox, rng):
    n = rnd(rng, 100, 10000)
    w(os.path.join(sandbox, "n.txt"), str(n) + "\n")
    steps = 0
    while n != 1:
        n = n//2 if n % 2 == 0 else 3*n + 1
        steps += 1
    return ("n.txt has a single integer n. Apply the Collatz sequence: if n is even, n=n/2;\n"
            "if odd, n=3n+1. Count how many steps until n reaches 1. Write that count to out.txt.\n",
            str(steps))

# ---- 37. GCD of a list ----
@task("gcd_list")
def t_gcd(sandbox, rng):
    import math
    vals = [rnd(rng, 1, 1000) for _ in range(20)]
    w(os.path.join(sandbox, "nums.txt"), "\n".join(map(str, vals)) + "\n")
    g = 0
    for v in vals:
        g = math.gcd(g, v)
    return ("nums.txt has one integer per line. Compute the GCD (greatest common divisor) of all\n"
            "the numbers. Write that GCD to out.txt.\n", str(g))

# ---- 38. LCM of a list ----
@task("lcm_list")
def t_lcm(sandbox, rng):
    import math
    vals = [rnd(rng, 1, 30) for _ in range(10)]
    w(os.path.join(sandbox, "nums.txt"), "\n".join(map(str, vals)) + "\n")
    def lcm(a, b): return a * b // math.gcd(a, b)
    r = 1
    for v in vals:
        r = lcm(r, v)
    return ("nums.txt has one integer per line. Compute the LCM (least common multiple) of all\n"
            "the numbers. Write that LCM to out.txt.\n", str(r))

# ---- 39. Roman numeral: sum ----
@task("roman_sum")
def t_roman(sandbox, rng):
    def to_roman(n):
        vals = [(1000,'M'),(900,'CM'),(500,'D'),(400,'CD'),(100,'C'),(90,'XC'),
                (50,'L'),(40,'XL'),(10,'X'),(9,'IX'),(5,'V'),(4,'IV'),(1,'I')]
        s = ""
        for v, s_ in vals:
            while n >= v:
                s += s_; n -= v
        return s
    romans = [to_roman(rnd(rng, 1, 3000)) for _ in range(30)]
    w(os.path.join(sandbox, "romans.txt"), "\n".join(romans) + "\n")
    def from_roman(s):
        m = {'I':1,'V':5,'X':10,'L':50,'C':100,'D':500,'M':1000}
        total = 0
        for i in range(len(s)):
            if i+1 < len(s) and m[s[i]] < m[s[i+1]]:
                total -= m[s[i]]
            else:
                total += m[s[i]]
        return total
    tot = sum(from_roman(r) for r in romans)
    return ("romans.txt has Roman numerals (one per line). Convert each to an integer and sum them.\n"
            "Write the total to out.txt.\n", str(tot))

# ---- 40. Binary: count 1-bits ----
@task("bit_count")
def t_bits(sandbox, rng):
    vals = [rnd(rng, 1, 100000) for _ in range(50)]
    w(os.path.join(sandbox, "nums.txt"), "\n".join(map(str, vals)) + "\n")
    tot = sum(bin(v).count("1") for v in vals)
    return ("nums.txt has one integer per line. For each number, count the number of 1-bits in its\n"
            "binary representation. Sum those counts across all numbers. Write the total to out.txt.\n",
            str(tot))

# ---- 41. XOR of a list ----
@task("xor_list")
def t_xor(sandbox, rng):
    vals = [rnd(rng, 1, 1000) for _ in range(30)]
    w(os.path.join(sandbox, "nums.txt"), "\n".join(map(str, vals)) + "\n")
    r = 0
    for v in vals:
        r ^= v
    return ("nums.txt has one integer per line. Compute the XOR of all the numbers.\n"
            "Write the result to out.txt.\n", str(r))

# ---- 42. Parentheses: max depth ----
@task("paren_depth")
def t_paren(sandbox, rng):
    # generate a balanced-ish expression with known max depth
    depth = rnd(rng, 2, 8)
    expr = "(" * depth + "x" + ")" * depth
    # add some noise
    expr = f"if ({expr} > 0) {{ return {expr}; }} else {{ return 0; }}"
    w(os.path.join(sandbox, "code.txt"), expr + "\n")
    # compute max depth
    cur = 0; mx = 0
    for ch in expr:
        if ch == "(":
            cur += 1; mx = max(mx, cur)
        elif ch == ")":
            cur -= 1
    return ("code.txt has a single line of code with parentheses.\n"
            "Find the MAXIMUM nesting depth of parentheses. Write that depth to out.txt.\n", str(mx))

# ---- 43. RPN evaluate ----
@task("rpn_eval")
def t_rpn(sandbox, rng):
    # generate a simple RPN expression
    a, b, c = rnd(rng,1,20), rnd(rng,1,20), rnd(rng,1,20)
    ops = ["+", "-", "*"]
    op1, op2 = pick(rng, ops), pick(rng, ops)
    rpn = f"{a} {b} {op1} {c} {op2}"
    w(os.path.join(sandbox, "expr.txt"), rpn + "\n")
    # evaluate
    def ev(rpn):
        st = []
        for t in rpn.split():
            if t in "+-*/":
                b_, a_ = st.pop(), st.pop()
                if t == "+": st.append(a_+b_)
                elif t == "-": st.append(a_-b_)
                elif t == "*": st.append(a_*b_)
                else: st.append(a_//b_)
            else:
                st.append(int(t))
        return st[0]
    result = ev(rpn)
    return ("expr.txt has a Reverse Polish Notation (RPN) expression: '<a> <b> <op1> <c> <op2>'.\n"
            "Evaluate it (it computes ((a op1 b) op2 c)). Write the integer result to out.txt.\n",
            str(result))

# ---- 44. Anagram groups ----
@task("anagram_groups")
def t_anagram(sandbox, rng):
    words = ["listen", "silent", "enlist", "hello", "world", "python", "nodejs", "rust", "go", "java"]
    lines = []
    for _ in range(50):
        lines.append(pick(rng, words))
    w(os.path.join(sandbox, "words.txt"), "\n".join(lines) + "\n")
    # count anagram groups (words that are anagrams of each other)
    from collections import defaultdict
    groups = defaultdict(list)
    for w_ in lines:
        groups[tuple(sorted(w_))].append(w_)
    n_groups = sum(1 for g in groups.values() if len(g) > 1)
    return ("words.txt has one word per line. Two words are anagrams if they have the same letters.\n"
            "Count how many ANAGRAM GROUPS there are (a group = 2+ words that are anagrams of each other).\n"
            "Write that count to out.txt.\n", str(n_groups))

# ---- 45. Missing number ----
@task("missing_number")
def t_missing(sandbox, rng):
    n = rnd(rng, 20, 100)
    full = set(range(1, n+1))
    missing = pick(rng, list(full))
    present = sorted(full - {missing})
    w(os.path.join(sandbox, "nums.txt"), "\n".join(map(str, present)) + "\n")
    return (f"nums.txt has one integer per line. The numbers should be 1 to N with exactly ONE\n"
            f"number missing. Find the missing number. Write it to out.txt.\n", str(missing))

# ---- 46. Duplicate number ----
@task("duplicate_number")
def t_dup_number(sandbox, rng):
    n = rnd(rng, 20, 100)
    vals = list(range(1, n+1))
    dup = pick(rng, vals)
    vals.append(dup)
    rng.shuffle(vals)
    w(os.path.join(sandbox, "nums.txt"), "\n".join(map(str, vals)) + "\n")
    return ("nums.txt has one integer per line. The numbers are 1 to N with exactly ONE number\n"
            "appearing TWICE. Find that duplicate number. Write it to out.txt.\n", str(dup))

# ---- 47. Subarray: count positive-sum subarrays ----
@task("pos_subarray")
def t_pos_sub(sandbox, rng):
    vals = [rnd(rng, -5, 5) for _ in range(50)]
    w(os.path.join(sandbox, "nums.txt"), "\n".join(map(str, vals)) + "\n")
    c = 0
    for i in range(len(vals)):
        s = 0
        for j in range(i, len(vals)):
            s += vals[j]
            if s > 0:
                c += 1
    return ("nums.txt has one integer per line (can be negative). Count the number of CONTIGUOUS\n"
            "subarrays whose sum is strictly positive (> 0). Write that count to out.txt.\n", str(c))

# ---- 48. Graph: connected components ----
@task("graph_components")
def t_graph(sandbox, rng):
    n_nodes = 50
    edges = []
    for _ in range(rnd(rng, 20, 40)):
        a, b = rnd(rng, 0, n_nodes-1), rnd(rng, 0, n_nodes-1)
        if a != b:
            edges.append((a, b))
    w(os.path.join(sandbox, "edges.txt"), "\n".join(f"{a} {b}" for a, b in edges) + "\n")
    # count connected components
    parent = list(range(n_nodes))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb: parent[ra] = rb
    for a, b in edges:
        union(a, b)
    n_comp = len(set(find(i) for i in range(n_nodes)))
    return ("edges.txt has lines '<a> <b>' representing undirected edges between nodes 0 to 49.\n"
            "Count the number of CONNECTED COMPONENTS in this graph. Write that count to out.txt.\n",
            str(n_comp))

# ---- 49. BFS shortest path ----
@task("bfs_shortest")
def t_bfs(sandbox, rng):
    import heapq
    from collections import defaultdict as _dd
    n_nodes = 30
    edges = []
    for _ in range(rnd(rng, 40, 80)):
        a, b = rnd(rng, 0, n_nodes-1), rnd(rng, 0, n_nodes-1)
        if a != b:
            edges.append((a, b, rnd(rng, 1, 20)))
    w(os.path.join(sandbox, "edges.txt"), "\n".join(f"{a} {b} {w_}" for a, b, w_ in edges) + "\n")
    src, dst = 0, n_nodes - 1
    # Dijkstra
    adj = _dd(list)
    for a, b, w_ in edges:
        adj[a].append((b, w_)); adj[b].append((a, w_))
    dist = {i: float("inf") for i in range(n_nodes)}
    dist[src] = 0
    pq = [(0, src)]
    while pq:
        d, u = heapq.heappop(pq)
        if d > dist[u]: continue
        for v, w_ in adj[u]:
            if d + w_ < dist[v]:
                dist[v] = d + w_
                heapq.heappush(pq, (d + w_, v))
    result = dist[dst] if dist[dst] != float("inf") else -1
    return ("edges.txt has lines '<a> <b> <weight>' representing undirected weighted edges.\n"
            f"Nodes are 0 to {n_nodes-1}. Find the SHORTEST PATH distance from node 0 to node {n_nodes-1}.\n"
            "Write that distance (or -1 if unreachable) to out.txt.\n", str(result))

# ---- 50. Multi-bug Python script ----
@task("multi_bug_script")
def t_multi_bug(sandbox, rng):
    nums = [rnd(rng, 1, 100) for _ in range(50)]
    w(os.path.join(sandbox, "data.txt"), "\n".join(map(str, nums)) + "\n")
    # script with 3 bugs:
    # 1. off-by-one in range
    # 2. wrong operator
    # 3. wrong variable
    w(os.path.join(sandbox, "solve.py"),
      """#!/usr/bin/env python3
# BUG: this should print the sum of all ODD numbers in data.txt
# There are 3 bugs.
total = 0
with open("data.txt") as f:
    lines = f.readlines()
for i in range(len(lines) - 1):  # BUG 1: off-by-one
    n = int(lines[i].strip())
    if n % 2 == 0:  # BUG 2: should be odd
        total += n * 2  # BUG 3: should just add n
print(total)
""")
    expected = sum(n for n in nums if n % 2 == 1)
    return ("solve.py is SUPPOSED to print the sum of all ODD numbers in data.txt.\n"
            "It has 3 bugs. Fix all 3, run it, and write the correct result to out.txt.\n",
            str(expected))

# ---------------------------------------------------------------------------
def main():
    random.seed(SEED)
    # Preserve any Phase-2 answers (out.txt / legacy out_<MODEL>.txt) across
    # re-runs, so fixing one task does not wipe solved work. (Input files are
    # always rebuilt.)
    saved_out = {}
    if os.path.exists(SB):
        for root, _, files in os.walk(SB):
            for fn in files:
                if fn == "out.txt" or (fn.startswith("out_") and fn.endswith(".txt")):
                    rel = os.path.relpath(root, SB)
                    saved_out[(rel, fn)] = open(os.path.join(root, fn)).read()
        shutil.rmtree(SB)
    os.makedirs(os.path.join(OUT, "questions"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "truth"), exist_ok=True)
    questions, truth = [], []
    for name, builder in TASKS:
        tid = f"term/{name}"
        sb = os.path.join(SB, name)
        os.makedirs(sb, exist_ok=True)
        rng = random.Random(SEED + zlib.crc32(name.encode()))
        md, expected = builder(sb, rng)
        w(os.path.join(sb, "task.md"), md)
        questions.append({"id": tid, "task": md.strip(), "sandbox": name})
        truth.append({"id": tid, "answer": expected})
    with open(os.path.join(OUT, "questions", "questions_terminal_hard.json"), "w") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, "truth", "truth_terminal_hard.json"), "w") as f:
        json.dump(truth, f, ensure_ascii=False, indent=2)
    for (rel, fn), content in saved_out.items():
        p = os.path.join(SB, rel, fn)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
    print(f"[ok] terminal-hard: {len(questions)} tasks -> {SB}" +
          (f"  (restored {len(saved_out)} out.txt)" if saved_out else ""))

if __name__ == "__main__":
    main()
