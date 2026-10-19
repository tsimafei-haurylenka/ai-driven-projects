"""PHASE 1 (terminal) — deterministic generator for the terminal-agent benchmark.

Creates 50 terminal tasks. Each task:
  - a sandbox dir  terminal_sandboxes/<task_id>/  with RANDOM input data (seed=1234)
  - a blind task.md (instructions only, NO answer)
  - a hidden expected answer (stored in truth/truth_terminal.json)

These are the CLEAN source sandboxes. run.sh copies each sandbox into a
per-run stage dir and mounts it writable as /tasks; the model (Phase 2) works
ONLY in that copy via the terminal and writes <sandbox>/out.txt with the exact
expected answer. Scoring (Phase 3) collects each out.txt into
runs/<RUN_NAME>/terminal/<task>.txt and compares it against the truth. Random
data means the answer cannot be memorized — it must be computed by actually
driving the shell.

Run:  ./.benchenv/bin/python prepare_terminal.py
"""
import json, os, random, hashlib, shutil, collections, zlib

OUT = os.path.dirname(os.path.abspath(__file__))
SB = os.path.join(OUT, "terminal_sandboxes")
SEED = 1234
N_TASKS = 50

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)

def rnd(rng, a, b):
    return rng.randint(a, b)

def pick(rng, seq):
    return seq[rng.randrange(len(seq))]

# ---------------------------------------------------------------------------
# task builders. Each returns (task_md, expected_out, setup_fn)
# setup_fn(sandbox, rng) writes the input files.
# ---------------------------------------------------------------------------
TASKS = []  # list of (name, builder)

def task(name):
    def deco(fn):
        TASKS.append((name, fn))
        return fn
    return deco

# ---- log analysis -------------------------------------------------------
@task("log_404_count")
def t_log_404(sandbox, rng):
    ips = [f"{rnd(rng,1,255)}.{rnd(rng,1,255)}.{rnd(rng,1,255)}.{rnd(rng,1,255)}" for _ in range(40)]
    statuses = [200, 200, 200, 301, 404, 404, 500, 200, 200, 404]
    lines = []
    for _ in range(500):
        ip = pick(rng, ips); st = pick(rng, statuses); sz = rnd(rng, 100, 9000)
        lines.append(f'{ip} - - [10/Oct/2023:{rnd(rng,0,23):02d}:{rnd(rng,0,59):02d}:{rnd(rng,0,59):02d} +0000] "GET /page{rnd(rng,1,50)} HTTP/1.1" {st} {sz}')
    w(os.path.join(sandbox, "access.log"), "\n".join(lines) + "\n")
    n404 = sum(1 for l in lines if f' {404} ' in l)
    return ("Count how many lines in access.log have HTTP status code 404.\n"
            "Write the count (a single integer) to out.txt.\n", str(n404))

@task("log_post_bytes")
def t_log_post(sandbox, rng):
    methods = ["GET", "GET", "GET", "POST", "POST", "PUT", "DELETE"]
    lines = []
    tot = 0
    for _ in range(400):
        m = pick(rng, methods); sz = rnd(rng, 100, 5000)
        lines.append(f'10.0.0.{rnd(rng,1,255)} - - [10/Oct/2023:12:00:00 +0000] "{m} /x HTTP/1.1" 200 {sz}')
        if m == "POST":
            tot += sz
    w(os.path.join(sandbox, "access.log"), "\n".join(lines) + "\n")
    return ("Sum the last field (bytes) of all lines in access.log whose HTTP method is POST.\n"
            "Write the total (a single integer) to out.txt.\n", str(tot))

@task("log_top_ip")
def t_log_top_ip(sandbox, rng):
    ips = [f"10.0.{rnd(rng,0,9)}.{rnd(rng,1,255)}" for _ in range(30)]
    # bias one ip
    hot = "10.0.7.77"
    lines = []
    for _ in range(600):
        ip = hot if rng.random() < 0.25 else pick(rng, ips)
        lines.append(f'{ip} - - [10/Oct/2023:00:00:00 +0000] "GET / HTTP/1.1" 200 100')
    w(os.path.join(sandbox, "access.log"), "\n".join(lines) + "\n")
    c = collections.Counter(l.split()[0] for l in lines)
    top = c.most_common(1)[0][0]
    return ("Find the IP address (first field) that appears most often in access.log.\n"
            "Write that IP (a single line) to out.txt.\n", top)

@task("log_hour_peak")
def t_log_hour(sandbox, rng):
    lines = []
    for _ in range(500):
        h = rnd(rng, 0, 23)
        lines.append(f'1.2.3.4 - - [10/Oct/2023:{h:02d}:00:00 +0000] "GET / HTTP/1.1" 200 100')
    w(os.path.join(sandbox, "access.log"), "\n".join(lines) + "\n")
    c = collections.Counter(int(l.split(":")[1]) for l in lines)
    peak = c.most_common(1)[0][0]
    return ("Requests are logged with a timestamp [..:HH:MM:SS ..]. Find the hour HH (00-23)\n"
            "with the most requests in access.log. Write that hour as a two-digit number to out.txt.\n",
            f"{peak:02d}")

@task("log_5xx_endpoints")
def t_log_5xx(sandbox, rng):
    eps = [f"/api/{rnd(rng,1,20)}" for _ in range(20)]
    lines = []
    cnt = collections.Counter()
    for _ in range(400):
        ep = pick(rng, eps); st = pick(rng, [200, 200, 200, 500, 502, 503])
        lines.append(f'1.1.1.1 - - [x] "GET {ep} HTTP/1.1" {st} 100')
        if st >= 500:
            cnt[ep] += 1
    w(os.path.join(sandbox, "access.log"), "\n".join(lines) + "\n")
    total = sum(cnt.values())
    return ("Count the total number of requests in access.log whose status code is 500 or higher.\n"
            "Write that count (a single integer) to out.txt.\n", str(total))

@task("log_unique_ua")
def t_log_ua(sandbox, rng):
    uas = [f"Agent-{i}" for i in range(15)]
    lines = []
    for _ in range(300):
        ua = pick(rng, uas)
        lines.append(f'1.1.1.1 - - [x] "GET / HTTP/1.1" 200 100 "{ua}"')
    w(os.path.join(sandbox, "access.log"), "\n".join(lines) + "\n")
    uas_seen = set(l.split('"')[-2] for l in lines)
    return ("Each line ends with a quoted user-agent. Count how many DISTINCT user-agents appear\n"
            "in access.log. Write that count (a single integer) to out.txt.\n", str(len(uas_seen)))

@task("log_avg_size")
def t_log_avg(sandbox, rng):
    sizes = [rnd(rng, 100, 1000) for _ in range(200)]
    lines = [f'1.1.1.1 - - [x] "GET / HTTP/1.1" 200 {s}' for s in sizes]
    w(os.path.join(sandbox, "access.log"), "\n".join(lines) + "\n")
    avg = sum(sizes) / len(sizes)
    return ("Compute the average (mean) of the last field (bytes) across all lines of access.log.\n"
            "Write it rounded to 2 decimal places to out.txt (e.g. 123.45).\n", f"{avg:.2f}")

@task("log_kth_size")
def t_log_kth(sandbox, rng):
    sizes = [rnd(rng, 1, 100000) for _ in range(100)]
    lines = [f'1.1.1.1 - - [x] "GET / HTTP/1.1" 200 {s}' for s in sizes]
    w(os.path.join(sandbox, "access.log"), "\n".join(lines) + "\n")
    kth = sorted(sizes)[4]  # 5th smallest
    return ("Look at the last field (bytes) of each line in access.log. Find the 5th SMALLEST value.\n"
            "Write that value (a single integer) to out.txt.\n", str(kth))

@task("log_emails")
def t_log_emails(sandbox, rng):
    names = [f"user{i}" for i in range(25)]
    doms = ["a.com", "b.org", "c.net"]
    lines = []
    for _ in range(300):
        em = f"{pick(rng, names)}@{pick(rng, doms)}"
        lines.append(f'login from {em} ok')
    w(os.path.join(sandbox, "auth.log"), "\n".join(lines) + "\n")
    emails = set(l.split()[2] for l in lines)
    return ("auth.log has one line per login: 'login from <email> ok'.\n"
            "Count how many DISTINCT email addresses appear. Write that count to out.txt.\n",
            str(len(emails)))

@task("log_status_dist")
def t_log_dist(sandbox, rng):
    sts = [200, 200, 200, 200, 301, 404, 500]
    lines = []
    for _ in range(400):
        st = pick(rng, sts)
        lines.append(f'1.1.1.1 - - [x] "GET / HTTP/1.1" {st} 100')
    w(os.path.join(sandbox, "access.log"), "\n".join(lines) + "\n")
    # status is the second-to-last field (the number just before the final size field)
    c = collections.Counter(l.split()[-2] for l in lines)
    top = c.most_common(1)[0][0]
    return ("Each line ends with '<status> <size>'. The HTTP status code is the SECOND-TO-LAST field\n"
            "(the number just before the final size field). Find the status code that appears MOST\n"
            "often in access.log. Write that status code (a single integer) to out.txt.\n", str(top))

# ---- CSV -----------------------------------------------------------------
def csv_header(cols):
    return ",".join(cols)

@task("csv_paid_sum")
def t_csv_paid(sandbox, rng):
    rows = []
    tot = 0
    for i in range(200):
        status = pick(rng, ["paid", "paid", "refunded", "pending"])
        amt = rnd(rng, 1, 500)
        rows.append(f"{i},item{i%10},{status},{amt}")
        if status == "paid":
            tot += amt
    w(os.path.join(sandbox, "orders.csv"), csv_header(["id", "item", "status", "amount"]) + "\n" + "\n".join(rows) + "\n")
    return ("orders.csv columns: id,item,status,amount (header row included).\n"
            "Sum the 'amount' column over all rows where status == 'paid'.\n"
            "Write the total (a single integer) to out.txt.\n", str(tot))

@task("csv_age_gt30")
def t_csv_age(sandbox, rng):
    rows = []
    n = 0
    for i in range(300):
        age = rnd(rng, 18, 70)
        rows.append(f"{i},name{i},{age}")
        if age > 30:
            n += 1
    w(os.path.join(sandbox, "people.csv"), csv_header(["id", "name", "age"]) + "\n" + "\n".join(rows) + "\n")
    return ("people.csv columns: id,name,age (header included).\n"
            "Count rows where age > 30. Write that count to out.txt.\n", str(n))

@task("csv_max_score")
def t_csv_max(sandbox, rng):
    rows = []
    best = (-1, "")
    for i in range(200):
        name = f"p{i}"; sc = rnd(rng, 0, 100)
        rows.append(f"{name},{sc}")
        if sc > best[0]:
            best = (sc, name)
    w(os.path.join(sandbox, "scores.csv"), csv_header(["name", "score"]) + "\n" + "\n".join(rows) + "\n")
    return ("scores.csv columns: name,score (header included).\n"
            "Find the row with the highest score. Write that score (a single integer) to out.txt.\n",
            str(best[0]))

@task("csv_cat_avg")
def t_csv_cat(sandbox, rng):
    cats = [f"c{i}" for i in range(6)]
    data = collections.defaultdict(list)
    rows = []
    for i in range(300):
        c = pick(rng, cats); p = rnd(rng, 1, 100)
        data[c].append(p); rows.append(f"{c},{p}")
    w(os.path.join(sandbox, "items.csv"), csv_header(["cat", "price"]) + "\n" + "\n".join(rows) + "\n")
    # category with highest average price
    best_cat = max(data.items(), key=lambda kv: sum(kv[1]) / len(kv[1]))[0]
    return ("items.csv columns: cat,price (header included).\n"
            "For each cat, compute the average price. Find the cat with the HIGHEST average.\n"
            "Write that cat name (a single line) to out.txt.\n", best_cat)

@task("csv_distinct_cat")
def t_csv_distinct(sandbox, rng):
    cats = [f"c{i}" for i in range(8)]
    rows = [f"{pick(rng, cats)},{rnd(rng,1,100)}" for _ in range(300)]
    w(os.path.join(sandbox, "items.csv"), csv_header(["cat", "price"]) + "\n" + "\n".join(rows) + "\n")
    n = len(set(r.split(",")[0] for r in rows))
    return ("items.csv columns: cat,price (header included).\n"
            "Count how many DISTINCT cat values appear. Write that count to out.txt.\n", str(n))

@task("csv_revenue")
def t_csv_rev(sandbox, rng):
    rows = []
    tot = 0
    for i in range(200):
        q = rnd(rng, 1, 10); p = rnd(rng, 1, 100)
        rows.append(f"prod{i%7},{q},{p}")
        tot += q * p
    w(os.path.join(sandbox, "sales.csv"), csv_header(["product", "qty", "price"]) + "\n" + "\n".join(rows) + "\n")
    return ("sales.csv columns: product,qty,price (header included).\n"
            "Compute total revenue = sum over all rows of (qty * price).\n"
            "Write the total (a single integer) to out.txt.\n", str(tot))

@task("csv_year2023")
def t_csv_year(sandbox, rng):
    rows = []
    n = 0
    for i in range(300):
        y = rnd(rng, 2020, 2024)
        rows.append(f"{i},{y}-01-01")
        if y == 2023:
            n += 1
    w(os.path.join(sandbox, "events.csv"), csv_header(["id", "date"]) + "\n" + "\n".join(rows) + "\n")
    return ("events.csv columns: id,date (header included). date is YYYY-MM-DD.\n"
            "Count rows where the date year is 2023. Write that count to out.txt.\n", str(n))

@task("csv_empty_email")
def t_csv_empty(sandbox, rng):
    rows = []
    n = 0
    for i in range(200):
        em = "" if rng.random() < 0.2 else f"u{i}@x.com"
        rows.append(f"{i},{em}")
        if em == "":
            n += 1
    w(os.path.join(sandbox, "users.csv"), csv_header(["id", "email"]) + "\n" + "\n".join(rows) + "\n")
    return ("users.csv columns: id,email (header included). Some email fields are empty.\n"
            "Count rows where the email field is empty. Write that count to out.txt.\n", str(n))

@task("csv_top_product")
def t_csv_top(sandbox, rng):
    prods = [f"p{i}" for i in range(10)]
    data = collections.Counter()
    rows = []
    for i in range(400):
        p = pick(rng, prods); q = rnd(rng, 1, 20)
        data[p] += q; rows.append(f"{p},{q}")
    w(os.path.join(sandbox, "sales.csv"), csv_header(["product", "qty"]) + "\n" + "\n".join(rows) + "\n")
    top = data.most_common(1)[0][0]
    return ("sales.csv columns: product,qty (header included).\n"
            "Sum qty per product. Find the product with the HIGHEST total qty.\n"
            "Write that product name (a single line) to out.txt.\n", top)

@task("csv_median")
def t_csv_median(sandbox, rng):
    vals = [rnd(rng, 1, 1000) for _ in range(101)]  # odd count
    rows = [f"{i},{v}" for i, v in enumerate(vals)]
    w(os.path.join(sandbox, "vals.csv"), csv_header(["id", "value"]) + "\n" + "\n".join(rows) + "\n")
    med = sorted(vals)[len(vals) // 2]
    return ("vals.csv columns: id,value (header included). There are 101 data rows.\n"
            "Compute the median of the 'value' column. Write that value (a single integer) to out.txt.\n",
            str(med))

# ---- JSON ----------------------------------------------------------------
@task("json_active_count")
def t_json_active(sandbox, rng):
    objs = [{"id": i, "active": rng.random() < 0.5} for i in range(200)]
    w(os.path.join(sandbox, "data.json"), json.dumps(objs, indent=1))
    n = sum(1 for o in objs if o["active"])
    return ("data.json is a JSON array of objects each with fields id and active (bool).\n"
            "Count objects where active == true. Write that count to out.txt.\n", str(n))

@task("json_price_sum")
def t_json_price(sandbox, rng):
    objs = [{"id": i, "price": rnd(rng, 1, 100)} for i in range(200)]
    w(os.path.join(sandbox, "data.json"), json.dumps(objs, indent=1))
    tot = sum(o["price"] for o in objs)
    return ("data.json is a JSON array of objects each with fields id and price (int).\n"
            "Sum all 'price' values. Write the total (a single integer) to out.txt.\n", str(tot))

@task("json_max_rating")
def t_json_rating(sandbox, rng):
    objs = [{"id": i, "rating": rnd(rng, 1, 5)} for i in range(200)]
    w(os.path.join(sandbox, "data.json"), json.dumps(objs, indent=1))
    top = max(o["rating"] for o in objs)
    return ("data.json is a JSON array of objects each with fields id and rating (int 1-5).\n"
            "Find the maximum rating. Write that value (a single integer) to out.txt.\n", str(top))

@task("json_distinct_tags")
def t_json_tags(sandbox, rng):
    tags = [f"t{i}" for i in range(12)]
    objs = [{"id": i, "tags": [pick(rng, tags) for _ in range(rnd(rng, 1, 3))]} for i in range(150)]
    w(os.path.join(sandbox, "data.json"), json.dumps(objs, indent=1))
    alltags = set(t for o in objs for t in o["tags"])
    return ("data.json is a JSON array of objects each with fields id and tags (array of strings).\n"
            "Count how many DISTINCT tag strings appear across all objects. Write that count to out.txt.\n",
            str(len(alltags)))

@task("json_avg_score")
def t_json_avg(sandbox, rng):
    objs = [{"id": i, "score": rnd(rng, 0, 100)} for i in range(200)]
    w(os.path.join(sandbox, "data.json"), json.dumps(objs, indent=1))
    avg = sum(o["score"] for o in objs) / len(objs)
    return ("data.json is a JSON array of objects each with fields id and score (int).\n"
            "Compute the average 'score'. Write it rounded to 2 decimal places to out.txt.\n",
            f"{avg:.2f}")

@task("json_shipped_ids")
def t_json_shipped(sandbox, rng):
    sts = ["shipped", "pending", "cancelled"]
    objs = [{"id": i, "status": pick(rng, sts)} for i in range(200)]
    w(os.path.join(sandbox, "data.json"), json.dumps(objs, indent=1))
    n = sum(1 for o in objs if o["status"] == "shipped")
    return ("data.json is a JSON array of objects each with fields id and status.\n"
            "Count objects where status == 'shipped'. Write that count to out.txt.\n", str(n))

@task("json_nested_count")
def t_json_nested(sandbox, rng):
    objs = []
    total_items = 0
    for i in range(50):
        items = [{"sku": f"s{j}"} for j in range(rnd(rng, 0, 5))]
        total_items += len(items)
        objs.append({"id": i, "items": items})
    w(os.path.join(sandbox, "data.json"), json.dumps(objs, indent=1))
    return ("data.json is a JSON array of objects; each has an 'items' array.\n"
            "Count the TOTAL number of items across all objects' 'items' arrays.\n"
            "Write that count to out.txt.\n", str(total_items))

@task("json_names_unique")
def t_json_names(sandbox, rng):
    names = [f"n{i}" for i in range(20)]
    objs = [{"id": i, "name": pick(rng, names)} for i in range(300)]
    w(os.path.join(sandbox, "data.json"), json.dumps(objs, indent=1))
    n = len(set(o["name"] for o in objs))
    return ("data.json is a JSON array of objects each with fields id and name.\n"
            "Count how many DISTINCT 'name' values appear. Write that count to out.txt.\n", str(n))

# ---- file operations -----------------------------------------------------
@task("file_count_tree")
def t_file_count(sandbox, rng):
    base = os.path.join(sandbox, "tree")
    n = 0
    for d in range(8):
        dd = os.path.join(base, f"d{d}")
        os.makedirs(dd, exist_ok=True)
        for f in range(rnd(rng, 1, 6)):
            w(os.path.join(dd, f"f{f}.txt"), f"content {rnd(rng,0,999)}\n")
            n += 1
    return ("In the directory 'tree' (with subdirectories), count the total number of FILES\n"
            "(not directories). Write that count to out.txt.\n", str(n))

@task("file_largest")
def t_file_largest(sandbox, rng):
    base = os.path.join(sandbox, "data")
    os.makedirs(base, exist_ok=True)
    best = (0, "")
    for f in range(15):
        sz = rnd(rng, 10, 10000)
        w(os.path.join(base, f"f{f}.bin"), "x" * sz)
        if sz > best[0]:
            best = (sz, f"f{f}.bin")
    return ("In the directory 'data' there are several .bin files of different sizes.\n"
            "Find the largest file by byte size. Write its size in bytes (a single integer) to out.txt.\n",
            str(best[0]))

@task("file_log_count")
def t_file_log(sandbox, rng):
    base = os.path.join(sandbox, "mixed")
    os.makedirs(base, exist_ok=True)
    nlog = 0
    for f in range(30):
        ext = pick(rng, ["log", "log", "txt", "csv", "json"])
        w(os.path.join(base, f"file{f}.{ext}"), "x\n")
        if ext == "log":
            nlog += 1
    return ("In the directory 'mixed' there are files with various extensions.\n"
            "Count how many files have the .log extension. Write that count to out.txt.\n", str(nlog))

@task("file_total_txt")
def t_file_txt(sandbox, rng):
    base = os.path.join(sandbox, "docs")
    os.makedirs(base, exist_ok=True)
    tot = 0
    for f in range(20):
        sz = rnd(rng, 1, 500)
        w(os.path.join(base, f"d{f}.txt"), "a" * sz)
        tot += sz
    return ("In the directory 'docs' there are .txt files.\n"
            "Compute the TOTAL size in bytes of all .txt files. Write that total to out.txt.\n", str(tot))

@task("file_dupes")
def t_file_dupes(sandbox, rng):
    base = os.path.join(sandbox, "files")
    os.makedirs(base, exist_ok=True)
    contents = [f"content-{i}\n" for i in range(10)]
    # write each content a random number of times
    for i, c in enumerate(contents):
        for k in range(rnd(rng, 1, 4)):
            w(os.path.join(base, f"f{i}_{k}.txt"), c)
    # count files whose content appears more than once
    from collections import Counter
    import hashlib as _h
    hc = Counter()
    for fn in os.listdir(base):
        hc[_h.md5(open(os.path.join(base, fn), 'rb').read()).hexdigest()] += 1
    dup_files = sum(c for c in hc.values() if c > 1)
    return ("In the directory 'files' there are .txt files, some with identical content.\n"
            "Count how many files have content that is DUPLICATED (i.e. at least one other file\n"
            "has exactly the same content). Write that count to out.txt.\n", str(dup_files))

@task("file_empty")
def t_file_empty(sandbox, rng):
    base = os.path.join(sandbox, "stuff")
    os.makedirs(base, exist_ok=True)
    n = 0
    for f in range(25):
        if rng.random() < 0.3:
            w(os.path.join(base, f"f{f}.txt"), "")
            n += 1
        else:
            w(os.path.join(base, f"f{f}.txt"), "data\n")
    return ("In the directory 'stuff' there are .txt files, some of which are empty (0 bytes).\n"
            "Count the empty files. Write that count to out.txt.\n", str(n))

@task("file_find_string")
def t_file_findstr(sandbox, rng):
    base = os.path.join(sandbox, "logs")
    os.makedirs(base, exist_ok=True)
    n = 0
    for f in range(20):
        lines = []
        for _ in range(rnd(rng, 5, 20)):
            line = f"line {rnd(rng,0,999)}"
            if rng.random() < 0.1:
                line = "ERROR something failed"
                n += 1
            lines.append(line)
        w(os.path.join(base, f"log{f}.txt"), "\n".join(lines) + "\n")
    return ("In the directory 'logs' there are several .txt files.\n"
            "Count the TOTAL number of lines (across all files) that contain the word 'ERROR'.\n"
            "Write that count to out.txt.\n", str(n))

# ---- text transformation -------------------------------------------------
@task("text_regex_count")
def t_text_regex(sandbox, rng):
    lines = []
    n = 0
    for _ in range(300):
        if rng.random() < 0.3:
            lines.append(f"order {rnd(rng,1000,9999)} placed")
            n += 1
        else:
            lines.append(f"random line {rnd(rng,0,999)}")
    w(os.path.join(sandbox, "orders.txt"), "\n".join(lines) + "\n")
    return ("orders.txt has many lines. Count the lines that match the pattern: a line that\n"
            "starts with 'order ' followed by exactly 4 digits. Write that count to out.txt.\n", str(n))

@task("text_unique_lines")
def t_text_unique(sandbox, rng):
    pool = [f"line-{i}" for i in range(40)]
    lines = [pick(rng, pool) for _ in range(300)]
    w(os.path.join(sandbox, "lines.txt"), "\n".join(lines) + "\n")
    n = len(set(lines))
    return ("lines.txt has many lines, many repeated. Count how many DISTINCT lines there are.\n"
            "Write that count to out.txt.\n", str(n))

@task("text_word_count")
def t_text_words(sandbox, rng):
    words = []
    for _ in range(500):
        words.append(f"w{rnd(rng,0,50)}")
    w(os.path.join(sandbox, "corpus.txt"), " ".join(words) + "\n")
    return ("corpus.txt is a single line of space-separated words.\n"
            "Count the total number of words. Write that count to out.txt.\n", str(len(words)))

@task("text_top_word")
def t_text_top(sandbox, rng):
    pool = [f"w{i}" for i in range(15)]
    words = [pick(rng, pool) for _ in range(500)]
    w(os.path.join(sandbox, "corpus.txt"), " ".join(words) + "\n")
    c = collections.Counter(words)
    top = c.most_common(1)[0][0]
    return ("corpus.txt is a single line of space-separated words.\n"
            "Find the word that appears MOST often. Write that word (a single line) to out.txt.\n", top)

@task("text_sort_middle")
def t_text_middle(sandbox, rng):
    nums = [rnd(rng, 1, 10000) for _ in range(99)]  # odd
    w(os.path.join(sandbox, "nums.txt"), "\n".join(map(str, nums)) + "\n")
    med = sorted(nums)[len(nums) // 2]
    return ("nums.txt has one integer per line (99 lines).\n"
            "Sort them and find the middle (median) value. Write that value to out.txt.\n", str(med))

# ---- git -----------------------------------------------------------------
@task("git_commit_count")
def t_git_count(sandbox, rng):
    import subprocess
    repo = os.path.join(sandbox, "repo")
    os.makedirs(repo, exist_ok=True)
    env = dict(os.environ, GIT_AUTHOR_NAME="dev", GIT_AUTHOR_EMAIL="d@x.com",
               GIT_COMMITTER_NAME="dev", GIT_COMMITTER_EMAIL="d@x.com")
    def git(*a):
        subprocess.run(["git", *a], cwd=repo, env=env, check=True,
                       capture_output=True)
    git("init", "-q")
    n = rnd(rng, 3, 8)
    for i in range(n):
        w(os.path.join(repo, "f.txt"), f"v{i}\n")
        git("add", "."); git("commit", "-q", "-m", f"commit {i}")
    return ("A git repository is at 'repo'. Count the total number of commits on the current branch.\n"
            "Write that count (a single integer) to out.txt.\n", str(n))

@task("git_lines_added")
def t_git_lines(sandbox, rng):
    import subprocess
    repo = os.path.join(sandbox, "repo")
    os.makedirs(repo, exist_ok=True)
    env = dict(os.environ, GIT_AUTHOR_NAME="dev", GIT_AUTHOR_EMAIL="d@x.com",
               GIT_COMMITTER_NAME="dev", GIT_COMMITTER_EMAIL="d@x.com")
    def git(*a):
        subprocess.run(["git", *a], cwd=repo, env=env, check=True, capture_output=True)
    git("init", "-q")
    w(os.path.join(repo, "a.txt"), "one\ntwo\n")
    git("add", "."); git("commit", "-q", "-m", "c0")
    nlines = rnd(rng, 5, 20)
    w(os.path.join(repo, "a.txt"), "\n".join(f"line{i}" for i in range(nlines)) + "\n")
    git("add", "."); git("commit", "-q", "-m", "c1")
    return ("A git repository is at 'repo' with 2 commits. Look at the LAST commit.\n"
            "Count how many lines were ADDED in that commit (git diff --stat / numstat).\n"
            "Write that count (a single integer) to out.txt.\n", str(nlines))

@task("git_branch_name")
def t_git_branch(sandbox, rng):
    import subprocess
    repo = os.path.join(sandbox, "repo")
    os.makedirs(repo, exist_ok=True)
    env = dict(os.environ, GIT_AUTHOR_NAME="dev", GIT_AUTHOR_EMAIL="d@x.com",
               GIT_COMMITTER_NAME="dev", GIT_COMMITTER_EMAIL="d@x.com")
    def git(*a):
        subprocess.run(["git", *a], cwd=repo, env=env, check=True, capture_output=True)
    git("init", "-q")
    w(os.path.join(repo, "a.txt"), "x\n")
    git("add", "."); git("commit", "-q", "-m", "c0")
    name = f"feature-{rnd(rng,100,999)}"
    git("checkout", "-q", "-b", name)
    w(os.path.join(repo, "b.txt"), "y\n")
    git("add", "."); git("commit", "-q", "-m", "c1")
    return ("A git repository is at 'repo'. The repository is currently on a feature branch.\n"
            "Find the name of the CURRENT branch. Write that branch name to out.txt.\n", name)

@task("git_author_commits")
def t_git_author(sandbox, rng):
    import subprocess
    repo = os.path.join(sandbox, "repo")
    os.makedirs(repo, exist_ok=True)
    def git(*a, name="alice", email="a@x.com"):
        env = dict(os.environ, GIT_AUTHOR_NAME=name, GIT_AUTHOR_EMAIL=email,
                   GIT_COMMITTER_NAME=name, GIT_COMMITTER_EMAIL=email)
        subprocess.run(["git", *a], cwd=repo, env=env, check=True, capture_output=True)
    git("init", "-q")
    n_alice = rnd(rng, 2, 6)
    for i in range(n_alice):
        w(os.path.join(repo, "f.txt"), f"a{i}\n")
        git("add", ".", name="alice"); git("commit", "-q", "-m", f"a{i}", name="alice")
    for i in range(rnd(rng, 2, 5)):
        w(os.path.join(repo, "f.txt"), f"b{i}\n")
        git("add", ".", name="bob"); git("commit", "-q", "-m", f"b{i}", name="bob")
    return ("A git repository is at 'repo' with commits by two authors: 'alice' and 'bob'.\n"
            "Count how many commits were authored by 'alice'. Write that count to out.txt.\n",
            str(n_alice))

@task("git_files_in_commit")
def t_git_files(sandbox, rng):
    import subprocess
    repo = os.path.join(sandbox, "repo")
    os.makedirs(repo, exist_ok=True)
    env = dict(os.environ, GIT_AUTHOR_NAME="dev", GIT_AUTHOR_EMAIL="d@x.com",
               GIT_COMMITTER_NAME="dev", GIT_COMMITTER_EMAIL="d@x.com")
    def git(*a):
        subprocess.run(["git", *a], cwd=repo, env=env, check=True, capture_output=True)
    git("init", "-q")
    nfiles = rnd(rng, 3, 8)
    for i in range(nfiles):
        w(os.path.join(repo, f"f{i}.txt"), f"v{i}\n")
    git("add", "."); git("commit", "-q", "-m", "initial")
    return ("A git repository is at 'repo' with a single commit that added several files.\n"
            "Count how many files were added in that (only) commit. Write that count to out.txt.\n",
            str(nfiles))

# ---- archive -------------------------------------------------------------
@task("tar_count")
def t_tar_count(sandbox, rng):
    import tarfile
    base = os.path.join(sandbox, "src")
    os.makedirs(base, exist_ok=True)
    n = 0
    for d in range(5):
        dd = os.path.join(base, f"d{d}")
        os.makedirs(dd, exist_ok=True)
        for f in range(rnd(rng, 1, 5)):
            w(os.path.join(dd, f"f{f}.txt"), "x\n")
            n += 1
    with tarfile.open(os.path.join(sandbox, "archive.tar"), "w") as tf:
        tf.add(base)
    return ("A tar archive 'archive.tar' is provided. Count the total number of FILES (not dirs)\n"
            "inside the archive. Write that count to out.txt.\n", str(n))

@task("tar_total_size")
def t_tar_size(sandbox, rng):
    import tarfile
    base = os.path.join(sandbox, "src")
    os.makedirs(base, exist_ok=True)
    tot = 0
    for f in range(10):
        sz = rnd(rng, 1, 500)
        w(os.path.join(base, f"f{f}.txt"), "a" * sz)
        tot += sz
    with tarfile.open(os.path.join(sandbox, "archive.tar"), "w") as tf:
        tf.add(base)
    return ("A tar archive 'archive.tar' is provided containing .txt files.\n"
            "Compute the TOTAL size in bytes of all files inside the archive. Write that to out.txt.\n",
            str(tot))

@task("tar_extract_count")
def t_tar_extract(sandbox, rng):
    import tarfile
    base = os.path.join(sandbox, "src")
    os.makedirs(base, exist_ok=True)
    n = 0
    for f in range(rnd(rng, 5, 15)):
        w(os.path.join(base, f"f{f}.dat"), "x\n")
        n += 1
    with tarfile.open(os.path.join(sandbox, "archive.tar"), "w") as tf:
        tf.add(base)
    return ("A tar archive 'archive.tar' is provided. Extract it and count how many .dat files\n"
            "it contains. Write that count to out.txt.\n", str(n))

# ---- scripting / debugging -----------------------------------------------
@task("script_fix")
def t_script_fix(sandbox, rng):
    nums = [rnd(rng, 1, 100) for _ in range(50)]
    # buggy script: off-by-one / wrong operator
    w(os.path.join(sandbox, "data.txt"), "\n".join(map(str, nums)) + "\n")
    w(os.path.join(sandbox, "solve.sh"),
      "#!/bin/bash\n# BUG: this is supposed to print the SUM of all numbers in data.txt\n"
      "total=0\nfor n in $(cat data.txt); do\n  total=$((total + n / 2))\ndone\necho $total\n")
    import subprocess
    os.chmod(os.path.join(sandbox, "solve.sh"), 0o755)
    expected = sum(nums)
    return ("There is a bash script 'solve.sh' that is SUPPOSED to print the sum of all numbers\n"
            "in data.txt, but it has a bug. Fix the script so it correctly prints the sum,\n"
            "run it, and write the correct sum (a single integer) to out.txt.\n", str(expected))

@task("script_write")
def t_script_write(sandbox, rng):
    rows = []
    tot = 0
    for i in range(100):
        a = rnd(rng, 1, 50); b = rnd(rng, 1, 50)
        rows.append(f"{a},{b}")
        if (a + b) % 2 == 0:
            tot += a + b
    w(os.path.join(sandbox, "pairs.csv"), csv_header(["a", "b"]) + "\n" + "\n".join(rows) + "\n")
    return ("pairs.csv columns: a,b (header included). Write a script (any language) that sums\n"
            "(a+b) over all rows where (a+b) is even. Run it and write the resulting sum\n"
            "(a single integer) to out.txt.\n", str(tot))

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
    with open(os.path.join(OUT, "questions", "questions_terminal.json"), "w") as f:
        json.dump(questions, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT, "truth", "truth_terminal.json"), "w") as f:
        json.dump(truth, f, ensure_ascii=False, indent=2)
    # restore preserved answers
    for (rel, fn), content in saved_out.items():
        p = os.path.join(SB, rel, fn)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f:
            f.write(content)
    print(f"[ok] terminal: {len(questions)} tasks -> {SB}" +
          (f"  (restored {len(saved_out)} out.txt)" if saved_out else ""))

if __name__ == "__main__":
    main()
