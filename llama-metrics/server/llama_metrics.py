#!/usr/bin/env python3
"""llama-metrics: HTTP + MCP metrics for llama-swap-managed inference.
Per-GPU tok/s comes from llama-swap /running (model->port) + per-model /logs/stream (unambiguous engine logs); GPU/CPU/RAM from /sys."""
import glob
import http.client
import json
import os
import re
import socket
import signal
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mcp.server.mcpserver import MCPServer

HTTP_PORT = 8090
MCP_PORT = 8091
SWAP_HOST = "127.0.0.1"
SWAP_PORT = 8080


def _load_config():
    """Optional config.json next to this file; missing/bad file = defaults."""
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "config.json")) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


_CONFIG = _load_config()

RUNNING_POLL_S         = float(_CONFIG.get("running_poll_s", 2.0))            # poll llama-swap /running
MERGE_S                = float(_CONFIG.get("merge_s", 1.0))                   # republish /perf
POLL_S                 = float(_CONFIG.get("poll_s", 3.0))                    # GPU/CPU/RAM refresh
DISCOVER_S             = float(_CONFIG.get("discover_s", 5.0))                # re-scan /proc for backend ports
STREAM_READ_TIMEOUT_S  = float(_CONFIG.get("stream_read_timeout_s", 30.0))    # idle SSE read timeout before reconnect
STREAM_FORCE_RECONNECT_S = float(_CONFIG.get("stream_force_reconnect_s", 120.0))  # hard reconnect cycle; replays are deduped

FRESH_RT_MS       = 10_000  # live gen/pp rate validity window
FRESH_PROGRESS_MS = 15_000  # prefill progress/ETA validity window
FRESH_FINAL_MS    = 60_000  # per-request finals hold window
FRESH_MTP_MS      = 120_000 # draft-acceptance hold window

WATCHDOG_S      = 15.0        # follower-thread liveness check
GPU_BUSY_UTIL   = 5.0
NVIDIA_SMI      = "nvidia-smi"
SYS_CACHE_TTL_S = 3.0         # TTL for expensive subprocess reads (sudo RAPL, nvidia-smi)
MAX_BODY        = 262144      # cap on upstream response bodies (256 KiB)

BACKEND_BASENAMES = {"llama-server", "llama_server", "vllm", "llama"}

# ---- published state ----
perf = {
    "gen": 0.0, "pp": 0.0, "source": "none",
    "mtp": 0.0, "mtp_valid": False,
    "model": None,
    "last_activity": 0, "last_gpu_busy": 0, "updated_at": 0,
}
gpu = {"gpus": [], "updated_at": 0}
sysinfo = {
    "cpu": 0.0, "cpu_cores": [],
    "ram_used": 0, "ram_total": 0,
    "cpu_temp": None, "cpu_power": None,
    "updated_at": 0,
}

_lock = threading.RLock()  # reentrant: handlers may call helpers that lock again
_cpu_prev = {}             # per-core load deltas for _read_cpu
_backends = {}      # port -> {"pid", "gpu", "cmd"} (process-derived GPU map)
_running = {}       # model_id -> {"port", "state", "description", "cmd", "seen_at"}
_streams = {}       # model_id -> {"js", "thread", "stop", "port"}
_listen_ports = {}  # socket inode -> port


def now_ms():
    return int(time.time() * 1000)


def _fetch_json(url, timeout=2.0):
    """GET a JSON doc from llama-swap. Fresh connection per call, bounded body:
    a hung, restarted, or half-dead upstream can never wedge us."""
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read(MAX_BODY))
    except Exception:
        return None

# ---- CPU temperature (read from /sys, no subprocess) ----
_cpu_temp_path = None


def find_cpu_temp_path():
    global _cpu_temp_path
    for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        try:
            with open(os.path.join(hwmon, "name")) as f:
                name = f.read().strip()
        except Exception:
            continue
        if name == "k10temp":
            p = os.path.join(hwmon, "temp1_input")
            if os.path.exists(p):
                _cpu_temp_path = p
                return p
    return None


def read_cpu_temp():
    global _cpu_temp_path
    if _cpu_temp_path is None:
        _cpu_temp_path = find_cpu_temp_path()
    if _cpu_temp_path is None:
        return None
    try:
        with open(_cpu_temp_path) as f:
            return int(f.read().strip()) / 1000.0
    except Exception:
        return None


def read_cpu_power():
    """Current CPU package power (W) via the RAPL helper (root, NOPASSWD sudo).

    Subprocess + sudo/pam churn is expensive; the panel consumes /sys every 3s,
    so results are cached for 3s to avoid spawning sudo once per poll.
    """
    now = time.time()
    if now - _cpu_power_cache["at"] < _SYS_CACHE_TTL_S:
        return _cpu_power_cache["val"]
    val = None
    try:
        out = subprocess.run(["sudo", "-n", "/usr/local/bin/cpu-power.sh"],
                             capture_output=True, text=True, timeout=3)
        v = out.stdout.strip()
        val = float(v) if v else None
    except Exception:
        val = None
    _cpu_power_cache["val"] = val
    _cpu_power_cache["at"] = now
    return val



# ---- local GPU / CPU / RAM readers (unchanged; engine-agnostic) ----
_SYS_CACHE_TTL_S = SYS_CACHE_TTL_S
_cpu_power_cache = {"val": None, "at": 0.0}
_nvidia_cache = {"gpus": [], "at": 0.0}



# ---- Vulkan device -> DRM card index mapping ----
# Vulkan device indices don't always match DRM card indices. Determine the
# actual mapping by reading the process's render device from /proc/<pid>/maps.
def _vulkan_to_card(pid):
    """Map a process's Vulkan device to its DRM card index.
    Reads /proc/<pid>/maps for /dev/dri/renderD*, then resolves the PCI address
    to find the matching card index."""
    try:
        with open(f"/proc/{pid}/maps") as f:
            maps = f.read()
    except Exception:
        return None
    # Find render device (e.g., /dev/dri/renderD128)
    m = re.search(r"/dev/dri/(renderD\d+)", maps)
    if not m:
        return None
    render_dev = m.group(1)
    # Resolve render device to PCI address via /dev/dri/by-path
    try:
        for link in glob.glob("/dev/dri/by-path/*-render"):
            target = os.path.basename(os.path.realpath(link))
            if target == render_dev:
                # Extract PCI address from symlink name
                pm = re.search(r"pci-([0-9a-f:.]+)", link)
                if pm:
                    pci_addr = pm.group(1)
                    # Find which card has this PCI address
                    for i in range(8):
                        card_path = f"/sys/class/drm/card{i}/device"
                        try:
                            real = os.path.realpath(card_path)
                            if pci_addr in real:
                                return i
                        except Exception:
                            continue
    except Exception:
        pass
    return None

def _pci_slot(path):
    try:
        real = os.path.realpath(path)
        m = re.findall(r"0000:[0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f]", real)
        return m[-1] if m else None
    except Exception:
        return None



# ---- GPU alias: short brand+model from PCI subsystem vendor + device ID ----
_SUBSYS_VENDOR_NAMES = {
    0x1458: "Gigabyte", 0x1da2: "Sapphire", 0x1043: "ASUS",
    0x196d: "Club 3D", 0x103c: "HP",
    0x10b5: "MSI", 0x1849: "ASRock", 0x106b: "Leadtek",
    0x107d: "Palit", 0x1462: "PNY", 0x14f6: "Zotac",
}
_DEVICE_SHORT_NAMES = {
    0x7550: "9070", 0x7551: "9070 XT", 0x7400: "9070 XT",
    0x7300: "7900 XTX", 0x744c: "7900 XT", 0x744d: "7900 XT",
    0x6f00: "7900 XTX", 0x6f10: "7900 XT",
}


def _gpu_alias(dev_path):
    """Short alias: Brand Model from PCI subsystem vendor + device ID."""
    try:
        with open(os.path.join(dev_path, "subsystem_vendor")) as f:
            vendor = int(f.read().strip(), 16)
        with open(os.path.join(dev_path, "device")) as f:
            device = int(f.read().strip(), 16)
    except Exception:
        return None
    brand = _SUBSYS_VENDOR_NAMES.get(vendor)
    model = _DEVICE_SHORT_NAMES.get(device)
    if brand and model:
        return brand + " " + model
    if brand:
        return brand + " " + format(device, "#x")
    return None


_gpu_name_map = {}
_gpu_name_map_loaded = False


def _load_gpu_name_map():
    global _gpu_name_map, _gpu_name_map_loaded
    if _gpu_name_map_loaded:
        return
    _gpu_name_map_loaded = True
    try:
        out = subprocess.run(["lspci"], capture_output=True, text=True, timeout=5).stdout
        for line in out.splitlines():
            m = re.match(r"([0-9a-f]{2}:[0-9a-f]{2}\.[0-9a-f])\s+VGA compatible controller.*?:\s+(.+)$", line)
            if not m:
                continue
            slot_short = m.group(1)
            name = m.group(2).strip()
            name = re.sub(r"\s*\[[0-9a-f]{4}:[0-9a-f]{4}\].*$", "", name).strip()
            _gpu_name_map["0000:" + slot_short] = name
    except Exception:
        pass


def _gpu_name(slot, fallback):
    _load_gpu_name_map()
    return _gpu_name_map.get(slot, fallback) if slot else fallback


def _read_sysfs_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _find_rocm_smi():
    import shutil
    p = shutil.which("rocm-smi")
    if p:
        return p
    cands = sorted(glob.glob("/opt/rocm*/bin/rocm-smi"))
    return cands[0] if cands else None


_rocm_smi = _find_rocm_smi()
_amd_gpu_names = None


def _load_amd_gpu_names():
    global _amd_gpu_names
    if _amd_gpu_names is not None:
        return _amd_gpu_names
    names = []
    if _rocm_smi:
        try:
            out = subprocess.run([_rocm_smi, "--showproductname"],
                                 capture_output=True, text=True, timeout=15).stdout
            for line in out.splitlines():
                m = re.search(r"Card Series:\s*(.+)$", line)
                if m:
                    names.append(m.group(1).strip())
        except Exception:
            pass
    _amd_gpu_names = names
    return names


def _find_amdgpu_hwmon_by_slot():
    mapping = {}
    for hwmon in sorted(glob.glob("/sys/class/hwmon/hwmon*")):
        try:
            with open(os.path.join(hwmon, "name")) as f:
                if f.read().strip() != "amdgpu":
                    continue
        except Exception:
            continue
        slot = _pci_slot(os.path.join(hwmon, "device"))
        if slot:
            mapping[slot] = hwmon
    return mapping


def _read_amd_gpus():
    gpus = []
    hwmon_by_slot = _find_amdgpu_hwmon_by_slot()
    for dev in sorted(glob.glob("/sys/class/drm/card[0-9]*/device")):
        busy = os.path.join(dev, "gpu_busy_percent")
        if not os.path.exists(busy):
            continue
        slot = _pci_slot(dev)
        util = _read_sysfs_int(busy) or 0
        vram_total = _read_sysfs_int(os.path.join(dev, "mem_info_vram_total")) or 0
        vram_used = _read_sysfs_int(os.path.join(dev, "mem_info_vram_used")) or 0
        g = {
            "name": _gpu_name(slot, "AMD GPU"),
            "util": float(util),
            "temp": 0.0, "temp_max": None, "temp_hotspot": 0.0,
            "vram_temp": 0.0,
            "vram_used": vram_used,
            "vram_total": vram_total,
            "power": 0.0,
            "fan": 0.0,
            "alias": _gpu_alias(dev),
        }
        hwmon = hwmon_by_slot.get(slot)
        if hwmon:
            def _t(label):
                for tp in sorted(glob.glob(os.path.join(hwmon, "temp*_input"))):
                    lp = os.path.join(os.path.dirname(tp),
                                      os.path.basename(tp).replace("_input", "_label"))
                    try:
                        with open(lp) as f:
                            if f.read().strip().lower() == label:
                                return _read_sysfs_int(tp)
                    except Exception:
                        continue
                return None
            edge = _t("edge"); junction = _t("junction"); mem = _t("mem")
            if edge is not None:
                g["temp"] = edge / 1000.0
            if junction is not None:
                g["temp_max"] = junction / 1000.0
                g["temp_hotspot"] = junction / 1000.0
            if mem is not None:
                g["vram_temp"] = mem / 1000.0
            fan_rpm = _read_sysfs_int(os.path.join(hwmon, "fan1_input"))
            fan_max = _read_sysfs_int(os.path.join(hwmon, "fan1_max"))
            if fan_rpm is not None and fan_max:
                g["fan"] = min(100.0, 100.0 * fan_rpm / fan_max)
            pwr = _read_sysfs_int(os.path.join(hwmon, "power1_average"))
            if pwr is not None:
                g["power"] = pwr / 1e6
        gpus.append(g)
    amd_names = _load_amd_gpu_names()
    for i, g in enumerate(gpus):
        if i < len(amd_names):
            g["name"] = amd_names[i]
    return gpus


def _read_nvidia_gpus():
    # nvidia-smi spawns NVML each call (heavy); cache for the panel's 3s cadence.
    now = time.time()
    if now - _nvidia_cache["at"] < _SYS_CACHE_TTL_S:
        return _nvidia_cache["gpus"]
    gpus = []
    try:
        out = subprocess.run(
            [NVIDIA_SMI, "--query-gpu=index,name,utilization.gpu,temperature.gpu,"
             "power.draw,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        for line in out.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 7:
                continue
            def _num(s):
                s = s.strip()
                if s in ("", "[N/A]", "N/A"):
                    return None
                try:
                    return float(s)
                except ValueError:
                    return None
            idx, name = parts[0], parts[1]
            util = _num(parts[2]) or 0.0
            temp = _num(parts[3]) or 0.0
            power = _num(parts[4]) or 0.0
            mem_used = (_num(parts[5]) or 0.0) * 1024 * 1024
            mem_total = (_num(parts[6]) or 0.0) * 1024 * 1024
            gpus.append({
                "name": name, "util": util, "temp": temp, "temp_max": None,
                "vram_temp": 0.0, "vram_used": mem_used, "vram_total": mem_total,
                "power": power, "fan": 0.0,
            })
    except Exception:
        pass
    _nvidia_cache["gpus"] = gpus
    _nvidia_cache["at"] = now
    return gpus


def _read_cpu():
    global _cpu_prev
    try:
        with open("/proc/stat") as f:
            lines = f.read().splitlines()
    except Exception:
        return 0.0, []
    sample = {}
    for line in lines:
        parts = line.split()
        if not parts or not parts[0].startswith("cpu"):
            continue
        vals = [int(x) for x in parts[1:]]
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        sample[parts[0]] = (idle, sum(vals))
    if not sample:
        return 0.0, []
    cores = []
    agg = 0.0
    prev = _cpu_prev
    for name in sorted(sample):
        idle, total = sample[name]
        p = prev.get(name)
        if p is None:
            continue
        d_idle = idle - p[0]; d_total = total - p[1]
        if d_total <= 0:
            continue
        pct = max(0.0, min(100.0, (1.0 - d_idle / d_total) * 100.0))
        if name == "cpu":
            agg = pct
        else:
            cores.append(pct)
    _cpu_prev = sample
    return agg, cores


def _read_ram():
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                k, _, v = line.partition(":")
                info[k.strip()] = int(v.strip().split()[0]) * 1024
        total = info.get("MemTotal", 0)
        avail = info.get("MemAvailable", info.get("MemFree", 0))
        return max(0, total - avail), total
    except Exception:
        return 0, 0


# ====================== BACKEND DISCOVERY + /metrics ======================
# Map socket inode -> listen port (from /proc/net/tcp + tcp6).
_listen_ports = {}


def _refresh_listen_ports():
    """Rebuild inode->listen-port map. Returns dict {port: count} of unique ports."""
    global _listen_ports
    _listen_ports = {}
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            lines = open(f).read().splitlines()[1:]
        except Exception:
            continue
        for line in lines:
            p = line.split()
            if len(p) < 10 or p[3] != "0A":
                continue
            try:
                inode = int(p[9])
                port = int(p[1].rsplit(":", 1)[-1], 16)
            except Exception:
                continue
            if port > 0:
                _listen_ports[inode] = port
    return dict(_listen_ports)


def _list_listen_ports():
    """All unique LISTEN ports on the box."""
    return sorted({v for v in _listen_ports.values()})


def _is_backend(cmd, exe):
    if "llama-swap" in cmd:
        return False
    base = os.path.basename(exe or "")
    if base in BACKEND_BASENAMES:
        return True
    if "llama-server" in cmd:
        return True
    # Strata engine (served via python wrapper + native binary)
    if "strata" in cmd.lower() and ("serve" in cmd or "--serve" in cmd):
        return True
    # vllm is often spawned via an interpreter.
    if re.search(r"\b(vllm\s+serve|vllm\.server|/vllm|serve.*--vllm)\b", cmd, re.I) or (
        "vllm" in cmd and base not in ("llama-swap", "")):
        return True
    return False



def poll_system():
    # Read outside the lock: subprocess-backed reads (RAPL, nvidia-smi) can
    # take seconds; holding the lock would stall every HTTP/MCP handler.
    now = now_ms()
    try:
        gpus = _read_amd_gpus() + _read_nvidia_gpus()
        any_busy = any(g.get("util", 0) > GPU_BUSY_UTIL for g in gpus)
        cpu_pct, cpu_cores = _read_cpu()
        ram_used, ram_total = _read_ram()
        cpu_temp = read_cpu_temp()
        cpu_power = read_cpu_power()
        with _lock:
            gpu["gpus"] = gpus
            gpu["updated_at"] = now
            for gi, g in enumerate(gpus):
                if g.get("util", 0) > GPU_BUSY_UTIL:
                    s = _session_for(gi)
                    s["temp"].append(g.get("temp", 0.0))
                    if g.get("temp_hotspot") is not None:
                        s["hot"].append(g["temp_hotspot"])
                    if g.get("power", 0) > 0:
                        s["pwr"].append(g["power"])
            if any_busy:
                perf["last_gpu_busy"] = now
            sysinfo["cpu"] = cpu_pct
            sysinfo["cpu_cores"] = cpu_cores
            sysinfo["ram_used"] = ram_used
            sysinfo["ram_total"] = ram_total
            sysinfo["cpu_temp"] = cpu_temp
            sysinfo["cpu_power"] = cpu_power
            sysinfo["updated_at"] = now
    except Exception as e:
        print(f"system poll error: {e}", flush=True)



# ====================== ENGINE LOG PARSERS (per-model) ======================
RE_TG = re.compile(r"tg =\s+([\d.]+) t/s(?:,\s*tg_3s =\s+([\d.]+) t/s)?")
RE_PROMPT = re.compile(r"prompt processing.*?/\s*([\d.]+) tokens per second")
RE_PROMPT_PROGRESS = re.compile(r"progress = ([\d.]+)")
RE_PROMPT_FULL = re.compile(
    r"prompt processing, n_tokens =\s*(\d+), progress = ([\d.]+), t =\s*([\d.]+) s / ([\d.]+) tokens per second")
RE_PROMPT_EVAL = re.compile(r"prompt eval time.*?\(\s*[\d.]+\s+ms per token,\s*([\d.]+)\s+tokens per second\)")
RE_EVAL = re.compile(r"eval time.*?\(\s*[\d.]+\s+ms per token,\s*([\d.]+)\s+tokens per second\)")
RE_DRAFT = re.compile(r"draft acceptance =\s*([\d.]+)")
TG_MAX = 2000     # above this, a tg sample is timing garbage, not a real speed
PP_MAX = 1_000_000
RE_STRATA_GEN = re.compile(
    r"\[strata\].*?:\s*([\d,]+)\s+of\s+(?:max\s+([\d,]+)|budget\s+[\d,]+\s*\(max\s+[\d,]+\))"
    r"\s+tokens,\s*([\d.]+)\s+tok/s")
RE_STRATA_PP = re.compile(
    r"\[strata\].*?reading the prompt:\s*([\d,]+)(?:\s+of\s+([\d,]+))?\s+tokens,\s*([\d.]+)\s+tok/s,\s*([\d.]+)\s+s")
RE_STRATA_DONE = re.compile(
    r"\[strata\].*?\bdone:\s*([\d,]+)\s+tokens in\s+[\d.,]+\s+s\s*\(([\d.]+)\s+tok/s\)")
RE_STRATA_REQ = re.compile(
    r"\[strata\].*?prefill\s+([\d.]+)\s+tok/s\s+decode\s+([\d.]+)\s+tok/s")
RE_STRATA_SERVE = re.compile(
    r"strata serve: prompt [\d,]+ tokens = [\d,]+ reused \+ (\d[\d,]*) read in ([\d.]+) ms"
    r" \(([\d.]+) tok/s\), (\d[\d,]*) generated in ([\d.]+) ms \(([\d.]+) tok/s\),"
    r" drafts accepted (\d[\d,]*) of (\d[\d,]*)")
PP_HISTORY_S = 30.0


def _num(s):
    return float(s.replace(",", ""))


def _compute_pp_eta(history):
    """ETA seconds to finish the prefill from progress samples (n, prog, t)."""
    if len(history) < 2:
        return None
    n_cur, prog, t_cur = history[-1][1], history[-1][2], history[-1][3]
    if prog <= 0.0 or prog >= 1.0:
        return None
    n_total = n_cur / prog
    if n_total <= n_cur + 1:
        return None
    S11 = S12 = S22 = S1t = S2t = 0.0
    for (_, n_i, _, t_i) in history:
        x1 = float(n_i); x2 = 0.5 * n_i * n_i
        S11 += x1 * x1; S12 += x1 * x2; S22 += x2 * x2
        S1t += x1 * t_i; S2t += x2 * t_i
    det = S11 * S22 - S12 * S12
    if abs(det) >= 1e-9:
        a = (S1t * S22 - S2t * S12) / det
        b = (S11 * S2t - S12 * S1t) / det
        if a >= 0 and b >= 0:
            def T(n):
                return a * n + 0.5 * b * n * n
            return round(max(0.0, T(n_total) - T(n_cur)), 1)
    # Linear fallback for near-linear prefills where the quadratic fit is ill-conditioned.
    now_cur, n_prev, now_prev = history[-1][0], history[-2][1], history[-2][0]
    dt = (now_cur - now_prev) / 1000.0
    dn = n_cur - n_prev
    if dt > 0.1 and dn > 0:
        rate = dn / dt
        if rate > 0:
            return round(max(0.0, (n_total - n_cur) / rate), 1)
    return None


def new_model_state():
    return {
        "gen": 0.0, "gen_at": 0, "pp": 0.0, "pp_at": 0,
        "pp_progress": 0.0, "pp_progress_at": 0, "pp_eta": None, "pp_hist": [],
        "gen_final": 0.0, "gen_final_at": 0, "pp_final": 0.0, "pp_final_at": 0,
        "mtp": 0.0, "mtp_valid": False, "mtp_at": 0,
        "tg_seq": 0, "tg_samples": deque(maxlen=500),
        "pp_seq": 0, "pp_samples": deque(maxlen=500),
        "last_activity": 0, "strata_pp_prev": None,
    }


def _sample_tg(js, v):
    if not (0 < v <= TG_MAX):  # junk from near-zero timing intervals (can reach 1e5+)
        return
    js["tg_samples"].append(round(v, 2))
    js["tg_seq"] += 1


def _sample_pp(js, v):
    if not (0 < v <= PP_MAX):  # PP legitimately bursts to 1e5 on cache loads; above that is junk
        return
    js["pp_samples"].append(round(v, 2))
    js["pp_seq"] += 1


def parse_strata(line, js):
    now = now_ms()
    m = RE_STRATA_SERVE.search(line)
    if m:
        js["pp_final"] = _num(m.group(3)); js["pp_final_at"] = now
        js["gen_final"] = _num(m.group(6)); js["gen_final_at"] = now
        _sample_pp(js, js["pp_final"]); _sample_tg(js, js["gen_final"])
        acc, off = _num(m.group(7)), _num(m.group(8))
        if off > 0:
            js["mtp"] = acc / off * 100.0; js["mtp_valid"] = True; js["mtp_at"] = now
        js["last_activity"] = now
        return
    m = RE_STRATA_PP.search(line)
    if m:
        n_read, n_total = _num(m.group(1)), (_num(m.group(2)) if m.group(2) else 0)
        speed, t_sofar = _num(m.group(3)), _num(m.group(4))
        # Delta between progress lines excludes the initial cache-read burst.
        prev = js.get("strata_pp_prev")
        live = speed
        if prev and n_read > prev[0] and t_sofar > prev[1]:
            live = (n_read - prev[0]) / (t_sofar - prev[1])
        js["strata_pp_prev"] = (n_read, t_sofar)
        js["pp"] = live; js["pp_at"] = now; js["last_activity"] = now
        _sample_pp(js, live)
        if n_total > 0:
            js["pp_progress"] = min(1.0, n_read / n_total)
            js["pp_progress_at"] = now
        hist = js["pp_hist"]
        hist.append((now, n_read, min(1.0, n_read / n_total) if n_total > 0 else 0.0, t_sofar))
        cutoff = now - int(PP_HISTORY_S * 1000)
        while hist and hist[0][0] < cutoff:
            hist.pop(0)
        js["pp_eta"] = _compute_pp_eta(hist)
        return
    m = RE_STRATA_REQ.search(line)
    if m:
        js["pp_final"] = _num(m.group(1)); js["pp_final_at"] = now
        js["gen_final"] = _num(m.group(2)); js["gen_final_at"] = now
        _sample_pp(js, js["pp_final"]); _sample_tg(js, js["gen_final"])
        js["last_activity"] = now
        js["strata_pp_prev"] = None
        return
    m = RE_STRATA_DONE.search(line)
    if m:
        js["gen_final"] = _num(m.group(2)); js["gen_final_at"] = now
        _sample_tg(js, js["gen_final"])
        js["last_activity"] = now
        return
    m = RE_STRATA_GEN.search(line)
    if m:
        js["gen"] = _num(m.group(3)); js["gen_at"] = now; js["last_activity"] = now
        _sample_tg(js, js["gen"])
        js["gen_line"] = line[:140]


def parse_line(line, js):
    """Parse one engine log line into this model's state (per-model stream = no cross-talk)."""
    if "[strata]" in line:
        parse_strata(line, js)
        return
    if not any(k in line for k in ("print_timing", "eval time", "tg =",
                                   "draft acceptance", "prompt processing")):
        return
    now = now_ms()
    m = RE_TG.search(line)
    if m:
        v = float(m.group(2) if m.group(2) is not None else m.group(1))
        if 0 < v <= TG_MAX:
            js["gen"] = v
            js["gen_at"] = now
            js["gen_line"] = line[:140]
        js["last_activity"] = now
        _sample_tg(js, v)
        return
    if "prompt processing" in line:
        m = RE_PROMPT.search(line)
        if m:
            js["pp"] = float(m.group(1)); js["pp_at"] = now; js["last_activity"] = now
            _sample_pp(js, js["pp"])
            mp = RE_PROMPT_PROGRESS.search(line)
            mf = RE_PROMPT_FULL.search(line)
            if mf:
                prog = float(mf.group(2))
                hist = js["pp_hist"]
                # Progress going backwards = new prefill: drop stale fit points.
                if hist and prog < hist[-1][2] - 0.05:
                    hist.clear()
                hist.append((now, int(mf.group(1)), prog, float(mf.group(3))))
                cutoff = now - int(PP_HISTORY_S * 1000)
                while hist and hist[0][0] < cutoff:
                    hist.pop(0)
                js["pp_progress"] = prog
                js["pp_progress_at"] = now
                js["pp_eta"] = _compute_pp_eta(hist)
            elif mp:
                js["pp_progress"] = float(mp.group(1))
                js["pp_progress_at"] = now
            return
    if "prompt eval time" in line:
        m = RE_PROMPT_EVAL.search(line)
        if m:
            js["pp_final"] = float(m.group(1)); js["pp_final_at"] = now
            _sample_pp(js, js["pp_final"])
            js["last_activity"] = now
            js["pp_hist"] = []
            return
    if "eval time" in line:
        m = RE_EVAL.search(line)
        if m:
            js["gen_final"] = float(m.group(1)); js["gen_final_at"] = now
            _sample_tg(js, js["gen_final"])
            js["last_activity"] = now
            return
    if "draft acceptance" in line:
        m = RE_DRAFT.search(line)
        if m:
            js["mtp"] = float(m.group(1)) * 100.0
            js["mtp_valid"] = True; js["mtp_at"] = now


# ====================== PER-MODEL LOG STREAM FOLLOWER ======================
def _follow_model(model_id):
    """Tail llama-swap's per-model log stream (history + live) and parse every timing line."""
    while True:
        with _lock:
            entry = _streams.get(model_id)
            if entry is None or entry["stop"].is_set():
                return
            js = entry["js"]
            entry["conns"] = entry.get("conns", 0) + 1
        # Fresh progress state per connection: history replay must not double-count.
        js["pp_hist"] = []
        js["strata_pp_prev"] = None
        entry.setdefault("seen", set())
        entry.setdefault("seen_q", deque())
        conn = None
        resp = None
        try:
            conn = http.client.HTTPConnection(SWAP_HOST, SWAP_PORT, timeout=10)
            conn.connect()
            # Read timeout set before getresponse: with Connection: close the
            # socket is dropped after the response, so it must be set now.
            conn.sock.settimeout(STREAM_READ_TIMEOUT_S)
            # Connection: close: no keep-alive state to wedge across a model
            # reload; every cycle is a clean reconnect (replay is deduped).
            conn.request("GET", "/logs/stream/" + urllib.parse.quote(model_id),
                         headers={"Connection": "close"})
            resp = conn.getresponse()
            if resp.status != 200:
                resp.read(256)
                time.sleep(3)
                continue
            entry["last_data_at"] = now_ms()
            last_data = time.time()
            conn_deadline = last_data + STREAM_FORCE_RECONNECT_S
            entry["conn_started"] = last_data
            while True:
                with _lock:
                    entry = _streams.get(model_id)
                    if entry is None or entry["stop"].is_set():
                        return
                if time.time() >= conn_deadline:
                    break  # hard cycle bounds any wedged-socket stall
                try:
                    line = resp.readline()
                except (socket.timeout, TimeoutError):
                    # A timed-out makefile is unrecoverable; reconnect (replay is deduped).
                    break
                if not line:
                    break
                text = line.decode("utf-8", "replace")
                entry["last_raw"] = text[:120]
                entry["last_data_at"] = now_ms()
                # After a model reload llama-swap keeps old SSE connections alive with
                # heartbeats but feeds them nothing; force a reconnect when data stalls.
                if text.startswith(":") or not text.strip():
                    if time.time() - last_data > 120:
                        break
                    continue
                last_data = time.time()
                # Reconnects replay the log buffer: never re-stamp a line already parsed,
                # otherwise idle models look freshly active forever.
                seen, seen_q = entry["seen"], entry["seen_q"]
                if text in seen:
                    entry["skipped"] = entry.get("skipped", 0) + 1
                    continue
                entry["parsed"] = entry.get("parsed", 0) + 1
                seen.add(text)
                seen_q.append(text)
                while len(seen_q) > 4000:
                    seen.discard(seen_q.popleft())
                try:
                    parse_line(text, js)
                except Exception as e:
                    print(f"parse error {model_id}: {e}", flush=True)
        except Exception:
            pass
        finally:
            for _c in (resp, conn):
                try:
                    if _c is not None:
                        _c.close()
                except Exception:
                    pass
        time.sleep(2)


# ====================== LLAMA-SWAP MONITOR (source of truth) ======================
def _port_from_proxy(proxy):
    m = re.search(r"://[^/:]+:(\d+)", proxy or "")
    return int(m.group(1)) if m else None


def _respawn_follower(mid, port, e):
    """Start a follower, carrying parse state and line dedupe so restarts
    neither reset session stats nor double-count log replays."""
    js = e["js"] if e is not None else new_model_state()
    entry = {"js": js, "stop": threading.Event(), "port": port, "thread": None}
    if e is not None:
        entry["seen"] = e.get("seen", set())
        entry["seen_q"] = e.get("seen_q", deque())
    _streams[mid] = entry
    t = threading.Thread(target=_follow_model, args=(mid,), daemon=True)
    entry["thread"] = t
    t.start()


def poll_running():
    """Refresh the set of running models from llama-swap /running and manage followers."""
    data = _fetch_json(f"http://{SWAP_HOST}:{SWAP_PORT}/running", timeout=3.0)
    if not data or "running" not in data:
        return
    running = data.get("running")
    if not isinstance(running, list) or len(running) > 64:
        return  # a broken upstream must not spawn a swarm of followers
    live = {}
    for item in running:
        if not isinstance(item, dict):
            continue
        mid = item.get("model") or item.get("name")
        port = _port_from_proxy(item.get("proxy") or "")
        if not mid or not port:
            continue
        live[str(mid)] = {
            "port": port,
            "state": item.get("state", ""),
            "description": item.get("description", ""),
            "cmd": (item.get("cmd") or "").replace("\n", " "),
            "seen_at": now_ms(),
        }
    with _lock:
        _running.clear()
        _running.update(live)
        gone = [mid for mid in _streams if mid not in live]
        for mid in gone:
            _streams[mid]["stop"].set()
        for mid, info in live.items():
            e = _streams.get(mid)
            if e is not None and not e["stop"].is_set() \
                    and e["thread"] is not None and e["thread"].is_alive():
                continue
            _respawn_follower(mid, info["port"], e)


# ====================== PORT -> GPU REGISTRY (process-derived) ======================
def _is_backend(cmd, exe):
    if "llama-swap" in cmd:
        return False
    base = os.path.basename(exe or "")
    if base in BACKEND_BASENAMES or "llama-server" in cmd:
        return True
    if "strata" in cmd.lower() and ("serve" in cmd or "--serve" in cmd):
        return True
    if re.search(r"\b(vllm\s+serve|vllm\.server|/vllm|serve.*--vllm)\b", cmd, re.I) or (
            "vllm" in cmd and base not in ("llama-swap", "")):
        return True
    return False


def discover_registry():
    """Map LISTEN ports -> {pid, gpu} for inference backends; prune dead entries."""
    listen = _refresh_listen_ports()  # inode -> port
    found = {}
    for pid in sorted(os.listdir("/proc")):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as fh:
                cmd = fh.read().decode("utf-8", "replace").replace("\0", " ")
        except Exception:
            continue
        try:
            exe = os.path.realpath(os.path.join("/proc", pid, "exe"))
        except Exception:
            exe = None
        if not _is_backend(cmd, exe):
            continue
        fd_inodes = set()
        try:
            for fd in glob.glob(f"/proc/{pid}/fd/*"):
                try:
                    link = os.readlink(fd)
                    m = re.search(r"socket:\[(\d+)\]", link)
                    if m:
                        fd_inodes.add(int(m.group(1)))
                except Exception:
                    continue
        except Exception:
            continue
        ports = sorted({listen[ino] for ino in fd_inodes if ino in listen})
        if not ports:
            continue
        gpu_idx = _vulkan_to_card(int(pid))
        if gpu_idx is None:  # wrapper processes (e.g. strata python -> native child)
            try:
                for child in open(f"/proc/{pid}/task/{pid}/children").read().split():
                    gpu_idx = _vulkan_to_card(int(child))
                    if gpu_idx is not None:
                        break
            except Exception:
                pass
        if gpu_idx is None:
            m_dev = re.search(r"--device\s+\S*?(\d+)", cmd)
            if m_dev:
                gpu_idx = int(m_dev.group(1))
        for port in ports:
            found[port] = {"pid": int(pid), "gpu": gpu_idx, "cmd": cmd[:120]}
    with _lock:
        _backends.clear()
        _backends.update(found)


def _model_gpu(port, description):
    """GPU index for a running model: process render-device first, brand-in-description fallback."""
    with _lock:
        b = _backends.get(port)
    if b and b.get("gpu") is not None:
        return b["gpu"]
    desc = (description or "").lower()
    with _lock:
        gpus = list(gpu.get("gpus", []))
    for i, g in enumerate(gpus):
        alias = (g.get("alias") or "").lower()
        brand = alias.split()[0] if alias else ""
        if brand and brand in desc:
            return i
    return None


# ====================== STRATA HTTP ENRICHMENT (progress % + drafts only) ======================
def enrich_strata(js, port):
    data = _fetch_json(f"http://{SWAP_HOST}:{port}/metrics", timeout=1.0)
    if not data:
        return
    now = now_ms()
    live = data.get("live", {}) or {}
    if live.get("state") == "reading":
        p_read = live.get("prompt_read") or 0
        p_total = live.get("prompt_total") or 0
        if p_total > 0:
            js["pp_progress"] = min(1.0, p_read / p_total)
            js["pp_progress_at"] = now
            mean = live.get("prefill_tok_s_mean")
            if mean:
                remaining = max(0, p_total - p_read)
                js["pp_eta"] = round(remaining / float(mean), 1)
    reqs = data.get("requests") or []
    if reqs:
        r0 = reqs[0]
        off = r0.get("drafts_offered") or 0
        acc = r0.get("drafts_accepted") or 0
        if off > 0 and acc > 0:
            js["mtp"] = acc / off * 100.0
            js["mtp_valid"] = True
            js["mtp_at"] = now


# ====================== MERGE + PUBLISH ======================
def merge_perf():
    """Publish per-GPU realtime metrics from per-model streams into perf."""
    now = now_ms()
    with _lock:
        running = dict(_running)
        streams = {mid: e["js"] for mid, e in _streams.items()}
        strata_ports = {mid: info["port"] for mid, info in running.items()
                        if "strata" in (info.get("cmd") or "").lower()}
    for mid, port in strata_ports.items():
        js = streams.get(mid)
        if js:
            try:
                enrich_strata(js, port)
            except Exception:
                pass

    by_gpu = {}
    instances = []
    pending = []
    best_mid, best_act = None, 0
    for mid, info in running.items():
        js = streams.get(mid)
        if js is None:
            continue
        gpu_idx = _model_gpu(info["port"], info.get("description"))
        # Phase gating: show a phase only while its own log lines are the newest,
        # so a lingering previous-phase value never masks the current one.
        gen = round(js["gen"], 2) if now - js["gen_at"] < FRESH_RT_MS and js["gen_at"] >= js["pp_at"] else 0.0
        pp = round(js["pp"], 2) if now - js["pp_at"] < FRESH_RT_MS and js["pp_at"] >= js["gen_at"] else 0.0
        prog_fresh = now - js["pp_progress_at"] < FRESH_PROGRESS_MS
        finals_ok = now - max(js["gen_final_at"], js["pp_final_at"]) < FRESH_FINAL_MS
        mtp_ok = js["mtp_valid"] and now - js["mtp_at"] < FRESH_MTP_MS
        entry = {
            "active": gen > 0 or pp > 0 or prog_fresh,
            "gen": gen, "gen_at": js["gen_at"], "gen_raw": round(js["gen"], 2),
            "pp": pp, "pp_at": js["pp_at"], "pp_raw": round(js["pp"], 2),
            "pp_progress": js["pp_progress"] if prog_fresh else 0.0,
            "pp_progress_at": js["pp_progress_at"] if prog_fresh else 0,
            "pp_eta": js["pp_eta"] if prog_fresh else None,
            "gen_final": round(js["gen_final"], 2) if finals_ok else 0.0,
            "gen_final_at": js["gen_final_at"],
            "pp_final": round(js["pp_final"], 2) if finals_ok else 0.0,
            "pp_final_at": js["pp_final_at"],
            "mtp": round(js["mtp"], 1) if mtp_ok else 0.0,
            "mtp_valid": mtp_ok,
            "tg_seq": js["tg_seq"], "tg_samples": list(js["tg_samples"]),
            "pp_seq": js["pp_seq"], "pp_samples": list(js["pp_samples"]),
            "model": mid,
        }
        instances.append({"port": info["port"], "model": mid,
                          "gen": gen, "pp": pp, "gpu": gpu_idx})
        # Prefer ready models for the headline: a stopping model is not serving.
        if info.get("state") == "ready" and js["last_activity"] > best_act:
            best_act, best_mid = js["last_activity"], mid
        # Only ready models publish per-GPU realtime: a starting/stopping model
        # would otherwise replay its last generation as a live speed during reload.
        if gpu_idx is not None and info.get("state") == "ready":
            cur = by_gpu.get(gpu_idx)
            if cur is None or js["last_activity"] >= cur.get("_act", 0):
                entry["_act"] = js["last_activity"]
                by_gpu[gpu_idx] = entry
        if gpu_idx is not None:
            pending.append((gpu_idx, mid, js))
    for e in by_gpu.values():
        e.pop("_act", None)

    top = by_gpu.get(0) or {}
    if best_mid is not None:
        info = running.get(best_mid, {})
        gidx = _model_gpu(info.get("port"), info.get("description"))
        top = by_gpu.get(gidx) or top
    realtime = {k: v for k, v in top.items() if k != "model"}
    last_act = max([js["last_activity"] for js in streams.values()] or [0])

    with _lock:
        perf["gen"] = top.get("gen", 0.0)
        perf["pp"] = top.get("pp", 0.0)
        perf["source"] = "stream" if running else "none"
        perf["mtp"] = top.get("mtp", 0.0)
        perf["mtp_valid"] = top.get("mtp_valid", False)
        perf["model"] = best_mid
        perf["last_activity"] = max(last_act, perf.get("last_gpu_busy", 0))
        perf["realtime"] = realtime
        perf["realtime_by_gpu"] = by_gpu
        perf["instances"] = instances
        perf["updated_at"] = now
        for gpu_idx, mid, js in pending:
            _push_samples(_session_for(gpu_idx), mid, js)


def perf_snapshot():
    with _lock:
        snap = dict(perf)
    snap["last_activity"] = max(snap.get("last_activity", 0),
                                perf.get("last_gpu_busy", 0))
    return snap


# ====================== WORKERS ======================
def swap_monitor():
    last_run = 0.0
    while True:
        t = time.time()
        if t - last_run >= RUNNING_POLL_S:
            poll_running()
            last_run = t
        merge_perf()
        time.sleep(MERGE_S)


def stream_watchdog():
    """Respawn followers whose threads died; hard reconnect cycles recover wedged ones."""
    while True:
        time.sleep(WATCHDOG_S)
        try:
            with _lock:
                live = dict(_running)
                stale = [mid for mid, e in _streams.items()
                         if mid in live and (e["stop"].is_set() or e["thread"] is None
                                             or not e["thread"].is_alive())]
                for mid in stale:
                    print(f"watchdog: respawning follower {mid}", flush=True)
                    _respawn_follower(mid, live[mid]["port"], _streams[mid])
        except Exception as ex:
            print(f"watchdog error: {ex}", flush=True)


def registry_worker():
    while True:
        try:
            discover_registry()
        except Exception as e:
            print(f"registry error: {e}", flush=True)
        time.sleep(DISCOVER_S)


def system_worker():
    while True:
        poll_system()
        time.sleep(POLL_S)

# ====================== SESSION AGGREGATION (/stats) ======================
_sessions = {}  # gpu_idx -> session aggregates; the server computes, the client renders


def _session_for(gpu_idx):
    s = _sessions.get(gpu_idx)
    if s is None:
        s = _sessions[gpu_idx] = {
            "model": None,
            "tg": deque(maxlen=500), "pp": deque(maxlen=500),
            "temp": deque(maxlen=500), "hot": deque(maxlen=500),
            "pwr": deque(maxlen=500),
            "tg_seq": 0, "pp_seq": 0,
        }
    return s


def _push_samples(sess, mid, js):
    # A model swap starts a fresh stats session on that GPU.
    if sess["model"] != mid:
        sess["model"] = mid
        sess["tg"].clear(); sess["pp"].clear()
        sess["tg_seq"] = 0; sess["pp_seq"] = 0
    n = js["tg_seq"] - sess["tg_seq"]
    if n > 0:
        for v in list(js["tg_samples"])[-n:]:
            sess["tg"].append(v)
        sess["tg_seq"] = js["tg_seq"]
    n = js["pp_seq"] - sess["pp_seq"]
    if n > 0:
        for v in list(js["pp_samples"])[-n:]:
            sess["pp"].append(v)
        sess["pp_seq"] = js["pp_seq"]


def _hist_bins(deq, width, bins):
    counts = [0] * bins
    for v in deq:
        i = int(v // width)
        counts[min(i, bins - 1)] += 1
    return counts


def _gauge_scale(deq):
    # 98th-percentile speed headroom for the TG bar; immune to single outliers.
    if not deq:
        return 1.0
    vals = sorted(deq)
    return round(vals[min(len(vals) - 1, int(len(vals) * 0.98))], 1) or 1.0


def _agg(deq, width):
    n = len(deq)
    if n == 0:
        return {"hist": [0] * 8, "hist_w": width, "n": 0, "mu": 0.0, "p95": 0.0}
    vals = sorted(deq)
    return {"hist": _hist_bins(deq, width, 8), "hist_w": width, "n": n,
            "mu": round(sum(vals) / n, 1),
            "p95": round(vals[min(n - 1, int(n * 0.95))], 1)}


def build_stats():
    now = now_ms()
    with _lock:
        gpus = gpu.get("gpus", [])
        rt = perf.get("realtime_by_gpu", {})
        out = []
        for gi, g in enumerate(gpus):
            idx = gi
            r = rt.get(str(idx)) or rt.get(idx) or {}
            s = _sessions.get(idx)
            gen_at, pp_at = r.get("gen_at", 0), r.get("pp_at", 0)
            # Display hold: a phase value lingers ~20 s after its last engine line.
            gen_disp = r.get("gen_raw", 0.0) if now - gen_at < 20000 else 0.0
            pp_disp = 0.0
            if r.get("pp_raw", 0.0) > 0 and now - pp_at < 20000 and pp_at >= gen_at:
                pp_disp = r["pp_raw"]
            tg_s = _agg(s["tg"], 10) if s else _agg(deque(), 10)
            pp_s = _agg(s["pp"], 100) if s else _agg(deque(), 100)
            out.append({
                "gpu": idx, "alias": g.get("alias"),
                "tg": dict(tg_s, display=round(gen_disp, 1),
                           session_max=_gauge_scale(s["tg"]) if s else 1.0),
                "pp": dict(pp_s, display=round(pp_disp, 1),
                           progress=r.get("pp_progress", 0.0), eta_s=r.get("pp_eta")),
                "mtp": r.get("mtp", 0.0), "mtp_valid": r.get("mtp_valid", False),
                "temp_mu": round(sum(s["temp"]) / len(s["temp"]), 1) if s and s["temp"] else 0.0,
                "hot_mu": round(sum(s["hot"]) / len(s["hot"]), 1) if s and s["hot"] else 0.0,
                "pwr_mu": round(sum(s["pwr"]) / len(s["pwr"]), 1) if s and s["pwr"] else 0.0,
            })
        return {"updated_at": now, "gpus": out}

# ====================== MCP ======================
mcp_server = MCPServer(name="llama-metrics", title="llama-metrics",
                       description="Live inference metrics: per-GPU gen/PP tok/s from llama-swap "
                                   "per-model logs, MTP accept %, active model; GPU util/temp/vram/power/fan.")


@mcp_server.tool()
def get_perf() -> str:
    """Return performance metrics as JSON: per-GPU gen tok/s, PP t/s, MTP accept %, active model, per-model instances."""
    return json.dumps(perf_snapshot())


@mcp_server.tool()
def get_gpu() -> str:
    """Return GPU metrics as JSON: per-GPU util %, temp, vram temp, vram used/total, power W, fan % (all detected GPUs)."""
    with _lock:
        return json.dumps(gpu)


@mcp_server.tool()
def get_sys() -> str:
    """Return system stats as JSON: CPU load % (aggregate + per-core), RAM used/total bytes, CPU temp °C."""
    with _lock:
        return json.dumps(sysinfo)


@mcp_server.tool()
def get_inference_stats() -> str:
    """Return per-GPU session aggregates as JSON: TG/PP histogram bins, n/mean/p95, held display values, gauge max, MTP %, mean busy GPU temps."""
    return json.dumps(build_stats())


def run_mcp():
    try:
        mcp_server.run(transport="streamable-http", host="0.0.0.0", port=MCP_PORT,
                       streamable_http_path="/mcp", stateless_http=True)
    except Exception as e:
        print(f"mcp server stopped: {e}", flush=True)


# ====================== HTTP ======================
class Handler(BaseHTTPRequestHandler):
    timeout = 10

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj):
        self._send(200, json.dumps(obj).encode(), "application/json")

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            self._route(path)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.close_connection = True

    do_HEAD = do_GET

    def _route(self, path):
        if path == "/perf":
            self._json(perf_snapshot())
        elif path == "/gpu":
            with _lock:
                self._json(dict(gpu))
        elif path == "/sys":
            with _lock:
                self._json(dict(sysinfo))
        elif path == "/stats":
            self._json(build_stats())
        elif path == "/debug":
            with _lock:
                now = now_ms()
                self._json({
                    "running": {mid: {k: v for k, v in info.items() if k != "cmd"}
                               for mid, info in _running.items()},
                    "backends": {str(p): {"gpu": b.get("gpu"), "pid": b.get("pid")}
                                 for p, b in _backends.items()},
                    "models": {mid: {
                        "gpu": _model_gpu(e["port"], _running.get(mid, {}).get("description", "")),
                        "conns": e.get("conns", 0),
                        "alive": bool(e["thread"] is not None and e["thread"].is_alive()),
                        "conn_age_s": round(time.time() - e.get("conn_started", 0), 1),
                        "last_data_age_ms": now - e.get("last_data_at", 0),
                        "last_raw": e.get("last_raw", ""), "skipped": e.get("skipped", 0), "parsed": e.get("parsed", 0), "seen": len(e.get("seen", ())),
                        "gen": e["js"]["gen"], "gen_age_ms": now - e["js"]["gen_at"],
                        "gen_line": e["js"].get("gen_line", ""),
                        "pp": e["js"]["pp"], "pp_age_ms": now - e["js"]["pp_at"],
                        "gen_final": e["js"]["gen_final"], "pp_final": e["js"]["pp_final"],
                        "mtp": round(e["js"]["mtp"], 1), "mtp_valid": e["js"]["mtp_valid"],
                        "last_activity_age_ms": now - e["js"]["last_activity"],
                    } for mid, e in _streams.items()},
                })
        elif path == "/health":
            self._send(200, b"ok", "text/plain")
        else:
            self._send(404, b"not found\n", "text/plain")

    def log_message(self, fmt, *args):
        pass


def run_http():
    srv = ThreadingHTTPServer(("0.0.0.0", HTTP_PORT), Handler)
    srv.daemon_threads = True
    print(f"llama-metrics HTTP on :{HTTP_PORT} (/perf, /gpu, /sys), MCP on :{MCP_PORT}", flush=True)
    srv.serve_forever()


def _warmup():
    # First reads are expensive (rocm-smi, nvidia-smi, /proc scan); run them
    # off the main thread so the HTTP/MCP servers come up immediately.
    discover_registry()
    poll_system()
    poll_running()
    merge_perf()


def main():
    def _term(signum, frame):
        print(f"llama-metrics: signal {signum}, shutting down", flush=True)
        os._exit(0)  # clean exit code for systemd; daemon threads die with us
    signal.signal(signal.SIGTERM, _term)
    threading.Thread(target=registry_worker, daemon=True).start()
    threading.Thread(target=system_worker, daemon=True).start()
    threading.Thread(target=run_mcp, daemon=True).start()
    threading.Thread(target=_warmup, daemon=True).start()
    threading.Thread(target=swap_monitor, daemon=True).start()
    threading.Thread(target=stream_watchdog, daemon=True).start()
    run_http()


if __name__ == "__main__":
    main()
