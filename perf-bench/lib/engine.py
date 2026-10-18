"""OpenAI-compatible endpoint probes and llama-bench wrapper."""
import json
import re
import subprocess
import time
import urllib.request
import uuid

from lib import util

CORPUS_UNIT = ("The quick brown fox reviews the quarterly logistics report of the "
               "distribution hub while the engineering team debates cache invalidation, "
               "tile ordering, and GPU kernel fusion strategies for inference servers. ")


def corpus_tokens(n, chars_per_token=4):
    need = max(n * chars_per_token, 40)
    return (CORPUS_UNIT * (need // len(CORPUS_UNIT) + 1))[:need]


class Probe:
    def __init__(self, base, api_key="", timeout=600):
        self.base = base.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout

    def _post(self, path, body, stream=False):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": "Bearer " + self.api_key} if self.api_key else {})})
        return time.time(), urllib.request.urlopen(req, timeout=self.timeout)

    def _chat(self, prompt, max_tokens, temperature=0.0, stream=False):
        body = {"model": self.model, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens, "temperature": temperature}
        if stream:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}
        return self._post("/v1/chat/completions", body)

    def run_case(self, name, suite, model, spec, params):
        self.model = model
        t = spec.get("type")
        rows = []
        if t == "health":
            rows.append(self.case_health(name, suite))
        elif t == "prefill":
            sizes = params["pp_sizes"] if params.get("force_pp") else (spec.get("tokens") or spec.get("sweep_values") or params["pp_sizes"])
            if not isinstance(sizes, list):
                sizes = [sizes]
            for n in sizes:
                rows.append(self.case_prefill(name, suite, model, int(n), spec))
        elif t == "decode":
            depths = params["depths"] if params.get("force_depths") else (spec.get("context_depth") or spec.get("sweep_values") or params["depths"])
            if not isinstance(depths, list):
                depths = [depths]
            for d in depths:
                rows.append(self.case_decode(name, suite, model, int(d), spec))
        elif t == "kv-cache":
            rows.extend(self.case_kv_cache(name, suite, model, spec, params))
        else:
            util.die(f"unknown case type '{t}' in {suite}/{name}")
        return [r for r in rows if r]

    def case_health(self, name, suite):
        t0 = time.time()
        try:
            r = urllib.request.urlopen(self.base + "/health", timeout=15)
            ok, code = True, r.getcode()
        except Exception:
            ok, code = False, None
        dt_h = time.time() - t0
        t1 = time.time()
        try:
            r = urllib.request.urlopen(self.base + "/v1/models", timeout=15)
            data = json.load(r)
            ids = [m.get("id") for m in data.get("data", [])]
        except Exception as e:
            ids, err = [], str(e)
        return {"test": suite + "/" + name, "phase": "health", "depth": 0,
                "metric": "health_ok", "value": 1 if ok else 0,
                "detail": f"code={code} health_s={dt_h:.3f} models={','.join(ids[:8])[:180]}"}

    def case_prefill(self, name, suite, model, tokens, spec):
        cpt = spec.get("chars_per_token", 4)
        prompt = f"P{uuid.uuid4().hex[:8]} " + corpus_tokens(tokens, cpt)
        try:
            t0, r = self._chat(prompt, 1)
            dt = time.time() - t0
            u = json.load(r).get("usage") or {}
            pt = u.get("prompt_tokens") or len(prompt) // cpt
            return {"test": suite + "/" + name, "phase": "prefill", "depth": pt,
                    "metric": "prefill_tps", "value": round(pt / max(dt, 0.01), 1),
                    "detail": f"target={tokens} prompt_tokens={pt} elapsed={dt:.2f}s"}
        except Exception as e:
            return {"test": suite + "/" + name, "phase": "prefill", "depth": tokens,
                    "metric": "prefill_tps", "value": 0, "detail": f"error={type(e).__name__}"}

    def case_decode(self, name, suite, model, depth, spec):
        cpt = spec.get("chars_per_token", 4)
        n = spec.get("tokens", 128)
        prompt = f"D{uuid.uuid4().hex[:8]} " + corpus_tokens(max(depth - 64, 64), cpt) + "\nAnswer briefly."
        try:
            t0, r = self._chat(prompt, n, temperature=0.6, stream=True)
            first = None
            comp = 0
            for raw in r:
                line = raw.decode().strip()
                if not line.startswith("data:") or "[DONE]" in line:
                    continue
                try:
                    c = json.loads(line[5:])
                except Exception:
                    continue
                if (c.get("usage") or {}).get("completion_tokens"):
                    comp = c["usage"]["completion_tokens"]
                d = (c.get("choices") or [{}])[0].get("delta", {})
                for k in ("content", "reasoning_content"):
                    if d.get(k):
                        if first is None:
                            first = time.time() - t0
            total = time.time() - t0
            gen = max(total - (first or 0), 0.05)
            return {"test": suite + "/" + name, "phase": "decode", "depth": depth,
                    "metric": "decode_tps", "value": round((comp or n) / gen, 1),
                    "first_token_s": round(first or -1, 2),
                    "detail": f"first_token_s={first or -1:.2f} total_s={total:.2f} completion={comp or n}"}
        except Exception as e:
            return {"test": suite + "/" + name, "phase": "decode", "depth": depth,
                    "metric": "decode_tps", "value": 0, "detail": f"error={type(e).__name__}"}

    def case_kv_cache(self, name, suite, model, spec, params):
        cpt = spec.get("chars_per_token", 4)
        prefix_n = spec.get("prefix_tokens") or max(params["pp_sizes"])
        hits = spec.get("hits", 3)
        out_tokens = spec.get("tokens", 32)
        prefix = "PREFIX-" + uuid.uuid4().hex[:10] + "\n" + corpus_tokens(prefix_n, cpt)
        rows = []
        try:
            t0, r = self._chat(prefix + "\nSummarize in one line.", 1)
            dt = time.time() - t0
            u = json.load(r).get("usage") or {}
            cold_pt = u.get("prompt_tokens") or len(prefix) // cpt
            cold = cold_pt / max(dt, 0.01)
            rows.append({"test": suite + "/" + name, "phase": "cache-cold-fill", "depth": cold_pt,
                         "metric": "prefill_tps", "value": round(cold, 1), "detail": f"prefix_tokens={prefix_n}"})
            best = 0
            for i in range(hits):
                t1, r2 = self._chat(prefix + f"\nQ{i} {uuid.uuid4().hex[:6]}: one line?", out_tokens)
                dt2 = time.time() - t1
                u2 = json.load(r2).get("usage") or {}
                hit = max(u2.get("prompt_tokens", 1), 1) * 1.0 / max(
                    (dt2 - out_tokens * 0.02), 0.01)  # prompt-side speed approximation
                best = max(best, hit)
            rows.append({"test": suite + "/" + name, "phase": "cache-hit", "depth": cold_pt,
                         "metric": "cache_hit_tps", "value": round(best, 1),
                         "detail": f"speedup_vs_cold={best / max(cold, 0.01):.1f}x hits={hits}"})
        except Exception as e:
            rows.append({"test": suite + "/" + name, "phase": "cache-hit", "depth": prefix_n,
                         "metric": "cache_hit_tps", "value": 0, "detail": f"error={type(e).__name__}"})
        return rows


ROW_RE = re.compile(r"\b(pp|tg)\s*(\d+)\b[^\d]*?([\d]+\.[\d]+)")


def run_llama_bench(exe, gguf, extra, tag, timeout):
    """Run llama.cpp's llama-bench binary and parse its pp/tg lines into rows."""
    cmd = [exe] + (["--model", gguf] if gguf else []) + (extra.split() if extra else [])
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        text = p.stdout + "\n" + p.stderr
    except Exception as e:
        return [{"test": "llama-bench/run", "phase": "run", "depth": 0, "metric": "status",
                 "value": 0, "detail": f"error={e}"}]
    rows = []
    for kind, n, val in ROW_RE.findall(text):
        rows.append({"test": "llama-bench", "phase": kind, "depth": int(n),
                     "metric": f"{kind}_tps", "value": float(val), "detail": f"tag={tag}"})
    if not rows:
        rows.append({"test": "llama-bench", "phase": "parse", "depth": 0, "metric": "status",
                     "value": 0, "detail": "no pp/tg lines found; raw tail: " + text[-300:]})
    return rows
