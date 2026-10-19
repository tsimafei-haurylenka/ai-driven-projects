# llama-metrics (server)

A read-only metrics service for llama.cpp / llama-swap / Strata deployments.
It publishes live inference and hardware metrics over HTTP and MCP so any
client can consume them. The [pi client](../pi/) renders these metrics as a
status bar inside the pi coding agent, but the server is independent of pi and
can be used by anything that speaks HTTP or MCP.

```
+----------------------------+
| GPU box (llama-swap)       |
|                            |
| llama-server (engines)     |
|          |                 |
|          v                 |
| llama_metrics.py           |
|  - :8090  HTTP API         |
|  - :8091  MCP endpoint     |
|                            |
| reads: /running,           |
| per-model /logs/stream,    |
| /sys (GPU, CPU, RAM)       |
+----------------------------+
```

## What it exposes

The server computes everything; clients only map numbers to their own display.
Per-GPU token-generation and prompt-processing rates, MTP draft-acceptance,
session histograms and n / mean / p95 aggregates, plus GPU / CPU / RAM
hardware state.

## Repository layout

```
llama-metrics/
├── install.sh                  # install / deploy / check / uninstall
└── server/
    ├── llama_metrics.py        # the metrics service (HTTP + MCP)
    ├── config.json             # server-side tuning (history window)
    ├── requirements.txt        # python deps (mcp, uvicorn)
    └── llama-metrics.service   # systemd unit template
```

## Tested environments

- **Python** — 3.10 or newer. Tested on 3.12.3.
- **OS** — Ubuntu 24.04.2 LTS on WSL2 (kernel
  `5.15.167.4-microsoft-standard-WSL2`).
- **CPU** — AMD Ryzen 7 7730U with Radeon Graphics.
- **RAM** — 9.7 GiB.
- **GPU** — AMD RX 9070 (the inference GPU, on the GPU box).

Other hardware / OS combinations may work but are untested. The server is
read-only and depends only on `/sys`, the `mcp` package, and llama-swap's HTTP
API, so it should run anywhere Python 3.10+ does.

## How it works

`llama_metrics.py` is a single-file Python service with no framework of its
own beyond the `mcp` package. It runs three background workers plus an HTTP
server:

1. **Registry worker** — polls llama-swap's `/running` endpoint (port 8080)
   to discover which models are loaded and on which engine port. It also
   scans `/proc` to map backend PIDs to GPUs.
2. **Stream followers** — for each running model it opens the engine's
   per-model `/logs/stream` (SSE) and parses the engine's own log lines to
   get unambiguous per-GPU generation and prompt-processing rates, MTP
   acceptance, and prefill progress/ETA. A watchdog reconnects dead streams.
3. **System worker** — reads GPU state (AMD via `/sys/class/drm`, NVIDIA via
   `nvidia-smi`), CPU load per core, RAM, CPU temperature and package power
   from `/sys` and RAPL.

The HTTP server publishes four JSON endpoints on **:8090** and an MCP
endpoint on **:8091**:

| Endpoint | Content |
|----------|---------|
| `GET /perf` | aggregate gen/PP tok/s, MTP %, active model, per-GPU realtime detail |
| `GET /gpu`  | per-GPU util, temps, VRAM, power, fan |
| `GET /sys`  | CPU load (aggregate + per-core), RAM, CPU temp, CPU power |
| `GET /stats`| per-GPU session aggregates: TG/PP histograms, n/μ/p95, held display values, mean busy temps |
| `GET /health` | liveness probe (`ok`) |
| `GET /debug` | internal state (streams, backends, parse counters) |

The MCP endpoint exposes the same data as tools (`get_perf`, `get_gpu`,
`get_sys`, `get_inference_stats`) for MCP-capable clients.

## Prerequisites

llama-metrics is a read-only observer. It does not start, stop, or configure
any inference engine — it only reads what is already running. Before
installing it, the following must already be in place on the server box:

1. **llama-swap** — the model orchestrator. llama-metrics discovers models
   from llama-swap's `/running` endpoint and tails its per-model log streams,
   so llama-swap must be installed, configured, and running.
   - Repo: <https://github.com/mostlygeek/llama-swap>
   - Install (macOS / Linux, Homebrew):
     ```
     brew tap mostlygeek/llama-swap
     brew install llama-swap
     ```
   - Or grab a pre-built binary from the
     [releases page](https://github.com/mostlygeek/llama-swap/releases), or
     build from source:
     ```
     git clone https://github.com/mostlygeek/llama-swap.git
     cd llama-swap && make clean all   # binary lands in build/
     ```
   - Configure it with a `config.yaml` that names at least one model and the
     engine command that serves it.

2. **An inference engine** — the binary that actually runs the model.
   llama-swap drives it; llama-metrics only reads its logs. Supported engines
   include:
   - **llama.cpp** — <https://github.com/ggml-org/llama.cpp>
     ```
     git clone https://github.com/ggml-org/llama.cpp
     cd llama.cpp
     cmake -B build -DGGML_HIP=ON        # -DGGML_CUDA=ON for NVIDIA
     cmake --build build --config Release --target llama-server
     ```
   - **Strata** — <https://github.com/Niko1221/Strata> (Python wrapper +
     native engine binary)
     ```
     git clone https://github.com/Niko1221/Strata
     cd Strata
     python serve/server.py --engine strata --config <model-config.json>
     ```

The engine command is declared inside llama-swap's `config.yaml` (the `cmd:`
field per model). llama-metrics parses that config to learn which models exist
and which engine serves each, then tails llama-swap's per-model
`/logs/stream` to read the engine's own timing lines. So the full chain —
engine installed, referenced in llama-swap's config, and llama-swap running —
must already be working before llama-metrics can show anything.

## Configuration

`config.json` holds server-side tuning (e.g. the prompt-processing history
window). It is optional: a missing or malformed file falls back to the
built-in defaults. Ports are fixed at build time: HTTP `8090`, MCP `8091`,
llama-swap discovered at `127.0.0.1:8080`.

## Installation

`install.sh` installs and configures the server. It is idempotent and safe to
re-run.

```
./install.sh [command] [options]

Commands:
  install     Install/update the server on the local machine (default)
  deploy      Push the repo to a remote server and run install there
  check       Probe the server endpoints
  uninstall   Remove the server

Options:
  --host HOST       deploy target / check host (required for deploy)
  --user USER       deploy ssh user (default: current user)
  --port PORT       Server port (default: 8090)
  --prefix DIR      Server install dir (default: ~/llama-metrics)
  --system          Install a *system* systemd unit (needs sudo)
  --no-systemd      Do not touch systemd; print manual run instructions
  --dry-run         Show what would be done without doing it
```

### Install

```
./install.sh install
```

Installs the python deps, copies the service files to `~/llama-metrics`, and
installs + starts a user systemd service. Use `sudo ./install.sh install
--system` for a system-wide unit.

The install is fully idempotent: on a fresh machine it creates everything from
scratch (install dir, venv, systemd unit); on an already-installed machine it
updates the service files, refreshes deps, rewrites the unit, and restarts the
service. A differing `llama_metrics.py` is backed up as
`llama_metrics.py.bak.<timestamp>` before being replaced, and `config.json` on
the server is never overwritten once it exists.

### Redeploying changes from the repo

The canonical workflow: edit files in this repo, then push them to the server
with one command — no manual scp/restart:

```
SSH_PASS=<ssh-password> SUDO_PASS=<sudo-password> \
  ./install.sh deploy --host 192.168.1.104 --user timoti
```

`deploy` rsyncs the repo files (excluding caches/venvs) into a staging dir on
the server, runs `install.sh install --system` there via sudo, then waits for
the service to answer and probes every endpoint. If the service does not come
up, the command fails loudly instead of reporting success. Credentials are
passed only through environment variables (`SSH_PASS` for ssh via sshpass,
`SUDO_PASS` for remote `sudo -S`; without `SUDO_PASS` an interactive sudo
prompt is shown) — never stored in the repo.

Typical change cycle:

```
# 1. edit server/llama_metrics.py in the repo
# 2. syntax check
python3 -m py_compile server/llama_metrics.py
# 3. push + install + verify in one step
SSH_PASS=... SUDO_PASS=... ./install.sh deploy --host 192.168.1.104 --user timoti
```

Rollback if a change misbehaves: the previous file is on the server as
`llama_metrics.py.bak.<timestamp>`; copy it over and run the deploy command
again, or run `install.sh install --system` directly on the server.

### Verify

```
./install.sh check --host localhost --port 8090
```

Probes every server endpoint. A passing check looks like:

```
==> Checking llama-metrics server on localhost:8090
  ok /health -> 200
  ok /perf -> 200
  ok /gpu -> 200
  ok /sys -> 200
  ok /stats -> 200
  ok check passed
```

## Client

The server is consumed by the [pi client](../pi/), which polls these endpoints
and paints a live status bar above the pi editor. Any HTTP or MCP client can
use the server the same way.
