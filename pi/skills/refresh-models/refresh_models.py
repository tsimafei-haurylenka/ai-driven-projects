#!/usr/bin/env python3
"""Sync llama-cpp model list + contexts from llama-swap config into pi/qwen/opencode configs.

Effective context = ctx-size / parallel from the llama-swap server config.
Run per root: python3 refresh_models.py --root <home> --swap <local path to config.yml> [--default MODEL]
"""
import argparse, copy, json, os, re, shutil, sys

DEFAULT_MODEL = "qwen3.8-27b-IQ3XXS-Uncensored"

MTP_SUFFIX = " (MTP+Vision)"

def _engine_flags(args):
    """(ctx, vision, mtp) from an engine args list (--max-context/--ctx-size/--mmproj/--mtp)."""
    ctx = vision = mtp = None
    for i, a in enumerate(args):
        if a in ("--max-context", "--ctx-size"):
            if i + 1 < len(args) and str(args[i + 1]).isdigit():
                ctx = int(args[i + 1])
        elif a == "--mmproj":
            vision = True
        elif a == "--mtp":
            mtp = True
        elif a == "--spec-type" and i + 1 < len(args) and "mtp" in str(args[i + 1]).lower():
            mtp = True
    return ctx, vision, mtp

def parse_swap(text, config_map=None):
    """model id -> {ctx, name, vision, mtp}.

    ctx = --ctx-size / --parallel from the cmd; models without it (e.g. Strata)
    resolve it from the engine JSON referenced by --config in the cmd.
    """
    config_map = config_map or {}
    models, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^  ([A-Za-z0-9._-]+):\s*$", line)
        if m:
            cur = m.group(1)
            continue
        if not cur:
            continue
        d = models.setdefault(cur, {})
        if m := re.search(r"--ctx-size\s+(\d+)", line):
            d["ctx"] = int(m.group(1))
        if m := re.search(r"--parallel\s+(\d+)", line):
            d["par"] = int(m.group(1))
        if m := re.match(r"^    name:\s*(.+)$", line):
            d["name"] = m.group(1).strip()
        if "--mmproj" in line:
            d["vision"] = True
        if ("--spec-type" in line and "mtp" in line.lower()) or re.search(r"--model\s+\S*[Mm][Tt][Pp]\.gguf", line):
            d["mtp"] = True
        if m := re.search(r"--config\s+(\S+)", line):
            d.setdefault("cfg", m.group(1))
    for n, v in models.items():
        if "ctx" in v:
            continue
        for p in ([v["cfg"], config_map.get(v["cfg"])]) if v.get("cfg") else []:
            if p and os.path.exists(p):
                try:
                    args = json.load(open(p)).get("args", [])
                except Exception:
                    continue
                ctx, vision, mtp = _engine_flags(args)
                if ctx:
                    v["ctx"] = ctx
                    v.setdefault("vision", vision is True)
                    v.setdefault("mtp", mtp is True)
                break
    return {n: {"ctx": v["ctx"] // v.get("par", 1), "name": v.get("name", n),
               "vision": v.get("vision", False), "mtp": v.get("mtp", False)}
            for n, v in models.items() if "ctx" in v}

def display_name(base, t):
    """Mark models that run MTP spec decoding and take image input."""
    base = base[:-len(MTP_SUFFIX)] if base.endswith(MTP_SUFFIX) else base
    return base + MTP_SUFFIX if t.get("vision") and t.get("mtp") else base

def backup(p):
    if os.path.exists(p):
        shutil.copyfile(p, p + ".bak-refresh")

def sync_pi_models(p, target, default):
    backup(p)
    d = json.load(open(p))
    ms = [m for m in d["providers"]["llama-cpp"]["models"] if m["id"] in target]
    for m in ms:
        t = target[m["id"]]
        m["contextWindow"] = t["ctx"]
        m["input"] = ["text", "image"] if t["vision"] else ["text"]
        m["name"] = display_name(m.get("name", t["name"]), t)
    have = {m["id"] for m in ms}
    for mid, t in target.items():
        if mid not in have:
            ms.append({
                "id": mid, "name": display_name(t["name"], t), "reasoning": True,
                "contextWindow": t["ctx"], "maxTokens": 65536,
                "input": ["text", "image"] if t["vision"] else ["text"],
                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                "thinkingLevelMap": {"low": "low", "medium": "medium", "high": "high"},
            })
    d["providers"]["llama-cpp"]["models"] = ms
    for k in list(d["providers"]):
        if k.endswith("-remote") and k[:-7] in d["providers"]:
            d["providers"][k]["models"] = copy.deepcopy(d["providers"][k[:-7]]["models"])
    json.dump(d, open(p, "w"), indent=2)

def sync_pi_settings(p, default):
    backup(p)
    d = json.load(open(p))
    d["defaultModel"] = default
    d.setdefault("modelThinkingLevels", {})[f"llama-cpp/{default}"] = "low"
    json.dump(d, open(p, "w"), indent=2)

def sync_qwen(p, target, default):
    backup(p)
    d = json.load(open(p))
    ms = d["modelProviders"]["llama-cpp"]
    tpl = next(m for m in ms if m.get("id") in target)
    new = []
    for m in ms:
        if m.get("id") not in target:
            continue
        t = target[m["id"]]
        m["generationConfig"]["contextWindowSize"] = t["ctx"]
        m["isVision"] = t["vision"]
        m["name"] = display_name(m.get("name", t["name"]), t)
        new.append(m)
    have = {m.get("id") for m in new}
    for mid, t in target.items():
        if mid not in have:
            e = json.loads(json.dumps(tpl))
            e["id"] = mid
            e["name"] = display_name(t["name"], t)
            e["isVision"] = t["vision"]
            e["generationConfig"]["contextWindowSize"] = t["ctx"]
            new.append(e)
    d["modelProviders"]["llama-cpp"] = new
    d.setdefault("model", {})["name"] = default
    json.dump(d, open(p, "w"), indent=2)

def sync_opencode(p, target, default):
    backup(p)
    d = json.load(open(p))
    ms = d["provider"]["llama-cpp"]["models"]
    new = {}
    for mid, mv in ms.items():
        if mid not in target:
            continue
        t = target[mid]
        mv["limit"]["context"] = t["ctx"]
        mv["name"] = display_name(mv.get("name", t["name"]), t)
        if t["vision"]:
            mv["modalities"] = {"input": ["text", "image"], "output": ["text"]}
        else:
            mv.pop("modalities", None)
        new[mid] = mv
    for mid, t in target.items():
        if mid not in new:
            mv = {
                "name": display_name(t["name"], t),
                "limit": {"context": t["ctx"], "output": 65536},
                "options": {"reasoningEffort": "low"},
            }
            if t["vision"]:
                mv["modalities"] = {"input": ["text", "image"], "output": ["text"]}
            new[mid] = mv
    d["provider"]["llama-cpp"]["models"] = new
    for k in list(d["provider"]):
        if k.endswith("-remote") and k[:-7] in d["provider"]:
            d["provider"][k]["models"] = copy.deepcopy(d["provider"][k[:-7]]["models"])
    d["model"] = f"llama-cpp/{default}"
    json.dump(d, open(p, "w"), indent=2)

def check(p, kind, target, default):
    d = json.load(open(p))
    errs = []
    ctx = {k: v["ctx"] for k, v in target.items()}
    if kind == "pi-models":
        ms = {m["id"]: m for m in d["providers"]["llama-cpp"]["models"]}
        for k, v in ctx.items():
            if ms.get(k)["contextWindow"] != v:
                errs.append(f"{k}={ms.get(k)['contextWindow']}!={v}")
        for k, t in target.items():
            if t["vision"] != ("image" in ms.get(k, {}).get("input", [])):
                errs.append(f"{k} image={t['vision']}")
        for k in target:
            if target[k]["vision"] and target[k]["mtp"] and not ms.get(k, {}).get("name", "").endswith(MTP_SUFFIX):
                errs.append(f"{k} mtp-name")
        extra = set(ms) - set(ctx)
        if extra:
            errs.append(f"extra:{sorted(extra)}")
    elif kind == "pi-settings":
        if d.get("defaultModel") != default:
            errs.append(f"default={d.get('defaultModel')}")
    elif kind == "qwen":
        ms = {m["id"]: m for m in d["modelProviders"]["llama-cpp"]}
        for k, v in ctx.items():
            if ms.get(k)["generationConfig"]["contextWindowSize"] != v:
                errs.append(f"{k}={ms.get(k)['generationConfig']['contextWindowSize']}!={v}")
        for k, t in target.items():
            if t["vision"] != ms.get(k, {}).get("isVision"):
                errs.append(f"{k} vision={t['vision']}")
        if d.get("model", {}).get("name") != default:
            errs.append(f"model={d.get('model')}")
    elif kind == "opencode":
        ms = {k: v for k, v in d["provider"]["llama-cpp"]["models"].items()}
        for k, v in ctx.items():
            if ms.get(k)["limit"]["context"] != v:
                errs.append(f"{k}={ms.get(k)['limit']['context']}!={v}")
        for k, t in target.items():
            if t["vision"] != ("image" in ms.get(k, {}).get("modalities", {}).get("input", [])):
                errs.append(f"{k} modalities={t['vision']}")
        if d.get("model") != f"llama-cpp/{default}":
            errs.append(f"model={d.get('model')}")
    print(("OK  " if not errs else "FAIL") + f" {kind:12} {p}" + (f"  {'; '.join(errs)}" if errs else ""))
    return not errs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True, help="home dir of the target machine (paths are relative to it)")
    ap.add_argument("--swap", required=True, help="local path to llama-swap config.yml")
    ap.add_argument("--default", default=DEFAULT_MODEL, help="default model id to set everywhere")
    ap.add_argument("--config-map", action="append", default=[], metavar="REMOTE=LOCAL",
                    help="resolve --config engine files fetched elsewhere (repeatable)")
    a = ap.parse_args()
    cmap = dict(kv.split("=", 1) for kv in a.config_map)
    target = parse_swap(open(a.swap).read(), cmap)
    if not target:
        sys.exit("no models parsed from swap config")
    default = a.default
    if default not in target:
        sys.exit(f"default model {default} not in swap config")
    jobs = [
        ("pi-models", f"{a.root}/.pi/agent/models.json", lambda p: sync_pi_models(p, target, default)),
        ("pi-settings", f"{a.root}/.pi/agent/settings.json", lambda p: sync_pi_settings(p, default)),
        ("qwen", f"{a.root}/.qwen/settings.json", lambda p: sync_qwen(p, target, default)),
        ("opencode", f"{a.root}/.config/opencode/opencode.json", lambda p: sync_opencode(p, target, default)),
    ]
    ok = True
    for kind, p, fn in jobs:
        try:
            fn(p)
            ok &= check(p, kind, target, default)
        except Exception as e:
            print(f"FAIL {kind:12} {p}  {e}")
            ok = False
    print(f"\n{len(target)} models, default={default}")
    for k in sorted(target, key=lambda x: target[x]["ctx"], reverse=True):
        print(f"  {k:32} {target[k]['ctx']}")
    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
