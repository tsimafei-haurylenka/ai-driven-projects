# pi (llama-metrics client)

The pi side of llama-metrics: a pi extension that renders live inference and
hardware metrics as a status bar above the editor, plus an optional pi skill.
It is independent of the [server](../llama-metrics/) — it only needs a running
llama-metrics server to talk to over HTTP.

## What it shows

The extension paints a panel above the editor, one block per GPU plus a system
line. Everything is computed server-side; the client only maps numbers to
colored glyphs.

```
Gigabyte 9070: [TG] ▕█████████▏ 46.9 t/s   (MTP 88.0%)  ▁▁▁▇█▂▁▁70+  n 500 μ 41  p95 50                                          [PP]                    0.0 t/s   ▅▃▇▇▅█▆▁700+  n 410 μ 385   p95 675
Gigabyte 9070: util 0%    63°C (67°C)  μC 59°C (73°C)  vram 70°C  15.2G/15.9G  17W  μW 127W  fan 62%

Sapphire 9070: [TG] ▕·········▏ 0.0 t/s                 ▁▃█▄▁▁▁▁70+  n 500 μ 27  p95 38                                          [PP]                    0.0 t/s   ▃▂▁▁▁▄▆█700+  n 394 μ 591   p95 871
Sapphire 9070: util 100%  45°C (50°C)  μC 49°C (59°C)  vram 48°C  15.5G/15.9G  60W  μW 116W  fan 0%

CPU  util 0%  61°C  RAM 40.1G/62.7G  30W
```

- **[TG]** — token generation: live tok/s, MTP draft-acceptance %, a 70 tok/s
  histogram of the session, and n / mean / p95 aggregates.
- **[PP]** — prompt processing: live t/s, prefill progress with ETA, a
  700 t/s histogram, and n / mean / p95 aggregates.
- **Hardware line** — util, chip temp (session max), session-mean chip/hotspot
  temps, VRAM temp, VRAM used/total, power, session-mean power, fan.
- **System line** — CPU util, CPU temp, RAM used/total, CPU package power.

Session aggregates (histograms, n/μ/p95, held display values, mean busy temps)
are collected server-side while the GPU is busy, so the panel stays meaningful
even between requests.

## Repository layout

```
pi/
├── install.sh                  # installs + configures the client
├── client/
│   └── llama-status.ts         # pi extension (the status bar)
├── skills/
│   └── refresh-models/         # optional pi skill (see below)
│       ├── SKILL.md
│       ├── refresh_models.py
│       └── refresh_models.sh
└── docs/
    ├── pi-with-llama-metrics-extension.png   # static screenshot
    ├── pi-with-llama-metrics-extension.svg   # animated (from screen recording)
    └── llama-metrics-status-bar-live.svg     # animated (live capture)
```

## How it works

`llama-status.ts` is a pi extension. On session start it registers a widget
above the editor and polls the server every second. It tries each configured
host in order and uses the first one that answers, so the same client works
on the LAN and from the internet without reconfiguration. All rendering math
is local; the server returns ready-made aggregates.

## Prerequisites

- **pi** — the [pi](https://pi.dev) coding agent, installed and on your PATH.
- **A running llama-metrics server** — see [../llama-metrics/](../llama-metrics/).
  The client has nothing to show until a server is up.

## Configuration

The extension reads a `llamaStatus` block in `~/.pi/agent/settings.json`.
Every key is optional; missing or invalid values fall back to built-in
defaults.

```json
{
  "llamaStatus": {
    "hosts": ["localhost", "127.0.0.1"],
    "port": 8090,
    "pollMs": 1000,
    "timeoutMs": 2500,
    "token": "optional-bearer-token"
  }
}
```

| Key | Type | Default | Meaning |
|-----|------|---------|---------|
| `hosts` | `string[]` | `["localhost", "127.0.0.1"]` | Ordered server addresses, tried top-down until one answers |
| `port` | `number` | `8090` | Service port |
| `pollMs` | `number` | `1000` | Poll interval (ms) |
| `timeoutMs` | `number` | `2500` | Per-request timeout (ms) |
| `token` | `string` | — | Optional bearer token sent as `Authorization` |

The default targets the local machine (server and client on the same box).
For a remote server, put its hostname or IP first in the list; the remaining
entries are automatic fallbacks. Edit the file and restart pi (or start a new
session) for changes to take effect.

## Installation

`install.sh` installs and configures the client. It is idempotent and safe to
re-run.

```
./install.sh [command] [options]

Commands:
  install     Install the client extension (default)
  skill       Install the refresh-models skill
  all         Install the extension and the skill
  check       Verify the client files and config
  uninstall   Remove the client extension and skill

Options:
  --host HOST       Server host the client should talk to (default: localhost)
  --port PORT       Server port (default: 8090)
  --token TOKEN     Bearer token sent by the client (optional)
  --pi-dir DIR      pi agent dir (default: ~/.pi/agent)
  --dry-run         Show what would be done without doing it
```

### Same machine as the server

```
./install.sh all
```

Installs the extension (copies `llama-status.ts` into
`~/.pi/agent/extensions/`, merges the `llamaStatus` block into
`settings.json`) and the skill.

### Different machine

```
# on the laptop / workstation:
./install.sh install --host <gpu-box-hostname>
```

The client reaches the server over the hostname you pass; the remaining
entries in `hosts` are automatic fallbacks.

### Verify

```
./install.sh check
```

Confirms the extension, the `llamaStatus` config, and the skill are in place.

## Optional: refresh-models skill

The `skills/refresh-models/` folder is a pi skill that syncs the local harness
model lists and context windows (pi, qwen, opencode) from the llama-swap
`config.yml` on the server. It is not required by the extension — install it
only if you want it:

```
./install.sh skill
```

Then ask pi to `/refresh_models` (or "refresh the model configs"). See
`skills/refresh-models/SKILL.md` for details.

## Documentation media

- `docs/pi-with-llama-metrics-extension.png` — static screenshot of pi with
  the extension active.
- `docs/pi-with-llama-metrics-extension.svg` — animated SVG (15 s loop) built
  from a screen recording of pi running with the extension.
- `docs/llama-metrics-status-bar-live.svg` — animated SVG (15 s loop) of a
  live capture of the status bar.
