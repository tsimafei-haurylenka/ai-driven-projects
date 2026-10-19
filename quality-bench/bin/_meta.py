#!/usr/bin/env python3
# usage: _meta.py <meta.json> [key=value ...]  merge values (ints parsed) into the JSON file
import json, sys
path = sys.argv[1]
try:
    m = json.load(open(path))
except Exception:
    m = {}
for kv in sys.argv[2:]:
    k, _, v = kv.partition("=")
    try:
        v = int(v)
    except ValueError:
        pass
    m[k] = v
json.dump(m, open(path, "w"), indent=1)
