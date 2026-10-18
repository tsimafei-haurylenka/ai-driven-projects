/**
 * llama-status extension
 *
 * Thin renderer: polls the llama-metrics service (/perf, /gpu, /sys, /stats) and paints a live
 * panel above the editor. All session math (histograms, n/μ/p95, holds, temp means) is computed
 * server-side; this file only maps numbers to colored glyphs. Server sources: llama-swap
 * /running + per-model /logs/stream. Config: ~/.pi/agent/settings.json -> "llamaStatus".
 */
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { visibleWidth, truncateToWidth } from "@earendil-works/pi-tui";
import { readFileSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

// ---- configuration (from ~/.pi/agent/settings.json -> "llamaStatus") ----
// Schema (all keys optional; missing/invalid values fall back to defaults):
//   hosts     string[]  ordered server addresses, tried top-down until one answers
//   port      number    service port
//   pollMs    number    poll interval in ms
//   timeoutMs number    per-request timeout in ms
//   token     string    optional bearer token sent as the Authorization header
interface LlamaStatusConfig {
  hosts: string[];
  port: number;
  pollMs: number;
  timeoutMs: number;
  token: string | null;
}

const DEFAULT_CONFIG: LlamaStatusConfig = {
  hosts: ["localhost", "127.0.0.1"],
  port: 8090,
  pollMs: 1000,
  timeoutMs: 2500,
  token: null,
};

function num(v: any, fallback: number): number {
  return typeof v === "number" && v > 0 ? v : fallback;
}

function loadConfig(): LlamaStatusConfig {
  let raw: any = {};
  try {
    raw = JSON.parse(readFileSync(join(homedir(), ".pi", "agent", "settings.json"), "utf8"));
  } catch {
    raw = {};
  }
  const cfg = raw.llamaStatus ?? {};
  const hosts = Array.isArray(cfg.hosts)
    ? cfg.hosts.filter((h: any) => typeof h === "string" && h.length > 0)
    : [];
  return {
    hosts: hosts.length > 0 ? hosts : DEFAULT_CONFIG.hosts,
    port: num(cfg.port, DEFAULT_CONFIG.port),
    pollMs: num(cfg.pollMs, DEFAULT_CONFIG.pollMs),
    timeoutMs: num(cfg.timeoutMs, DEFAULT_CONFIG.timeoutMs),
    token: typeof cfg.token === "string" && cfg.token ? cfg.token : null,
  };
}

const config = loadConfig();
const POLL_MS = config.pollMs;
const OFFLINE_AFTER_MS = 10000;

// Ordered endpoint list derived from config: one base URL per host, tried in order.
const ENDPOINTS = config.hosts.map((h) => `http://${h}:${config.port}`);

// Auth header, present only when a token is configured.
const AUTH_HEADERS: Record<string, string> | undefined = config.token
  ? { Authorization: `Bearer ${config.token}` }
  : undefined;

// ---- payload interfaces ----
interface Gpu {
  name: string;
  util: number;
  temp: number;
  temp_max: number | null;
  vram_temp: number;
  temp_hotspot?: number;
  vram_used: number;
  vram_total: number;
  power: number;
  fan: number;
  alias?: string;
}

interface GpuData {
  gpus: Gpu[];
  updated_at: number;
}

interface Sys {
  cpu: number;
  ram_used: number;
  ram_total: number;
  cpu_temp: number | null;
  cpu_power: number | null;
  updated_at: number;
}

interface Agg {
  hist: number[];
  hist_w: number;
  n: number;
  mu: number;
  p95: number;
}

interface StatsGpu {
  gpu: number;
  alias?: string;
  tg: Agg & { display: number; session_max: number };
  pp: Agg & { display: number; progress: number; eta_s: number | null };
  mtp: number;
  mtp_valid: boolean;
  temp_mu: number;
  hot_mu: number;
  pwr_mu: number;
}

interface Stats {
  updated_at: number;
  gpus: StatsGpu[];
}

const state: {
  gpu: GpuData | null;
  sys: Sys | null;
  stats: Stats | null;
  downSince: number;
  busy: boolean;
} = {
  gpu: null,
  sys: null,
  stats: null,
  downSince: 0,
  busy: false,
};

// ---- helpers ----
function gb(n: number): string {
  return (n / 1024 ** 3).toFixed(1) + "G";
}

function band(theme: any, text: string, v: number, mid: number, high: number): string {
  if (v < mid) return theme.fg("success", text);
  if (v < high) return theme.fg("warning", text);
  return theme.fg("error", text);
}

function perfBand(theme: any, text: string, v: number, low: number, high: number): string {
  if (v < low) return theme.fg("error", text);
  if (v < high) return theme.fg("warning", text);
  return theme.fg("success", text);
}

const FAST = 50;
const MED = 20;
const GAUGE_LEN = 9;
const GAUGE_FLOOR = 40;

const BLOCKS = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"];
const HBLOCKS = [" ", "▏", "▎", "▍", "▌", "▋", "▊", "▉"];
const TRACK = "·";

function fmt(v: number): string {
  if (v < 10) return v.toFixed(1);
  return v.toFixed(0);
}

function speedColor(tps: number, text: string, theme: any): string {
  if (tps >= FAST) return theme.fg("success", text);
  if (tps >= MED) return theme.fg("warning", text);
  return theme.fg("error", text);
}

function gauge(tps: number, theme: any, sessionMax: number): string {
  const scale = Math.max(sessionMax, GAUGE_FLOOR);
  let frac = scale > 0 ? tps / scale : 0;
  frac = Math.min(1, Math.max(0, frac));
  const eighths = Math.round(frac * GAUGE_LEN * 8);
  const full = (eighths / 8) | 0;
  const rem = eighths % 8;
  let fill = "█".repeat(full);
  let used = full;
  if (full < GAUGE_LEN && rem > 0) {
    fill += HBLOCKS[rem];
    used++;
  }
  return theme.fg("dim", "▕") + speedColor(tps, fill, theme) + theme.fg("dim", TRACK.repeat(GAUGE_LEN - used) + "▏");
}

const SPEED_W = 9;
function speedText(tps: number, theme: any, colorFor: (tps: number, t: string) => string): string {
  const dim = (s: string) => theme.fg("dim", s);
  const t = tps >= 1000 ? `${Math.round(tps)} t/s` : `${tps.toFixed(1)} t/s`;
  return (tps > 0 ? colorFor(tps, t) : dim(t)) + " ".repeat(Math.max(0, SPEED_W - t.length));
}

// Server returns bin counts + bin width; bars are colored by the bin's center value.
function histBars(theme: any, agg: Agg, colorFor: (tps: number, text: string) => string): string {
  const dim = (s: string) => theme.fg("dim", s);
  const bins = agg.hist.length;
  const right = `${(bins - 1) * agg.hist_w}+`;
  if (agg.n === 0) return dim(TRACK.repeat(bins) + right);
  const max = Math.max(...agg.hist);
  let out = "";
  for (let i = 0; i < bins; i++) {
    const norm = max === 0 ? 0 : Math.min(7, Math.round((agg.hist[i] / max) * 7));
    out += colorFor((i + 0.5) * agg.hist_w, BLOCKS[norm]);
  }
  return out + dim(right);
}

// Two-pass: build raw TG/PP sections per GPU, then pad each to the max width
// across all GPUs so columns align regardless of content (MTP, digit widths, progress).
function padVisible(s: string, w: number): string {
  const vw = visibleWidth(s);
  return s + " ".repeat(Math.max(0, w - vw));
}

function statsFor(theme: any, agg: Agg, colorFor: (tps: number, text: string) => string, slots: [number, number, number]): string {
  const dim = (s: string) => theme.fg("dim", s);
  let out = "";
  out += padVisible(agg.n > 0 ? ` ${dim(`n ${agg.n}`)}` : "", slots[0]);
  out += padVisible(agg.mu > 0 ? ` ${dim("μ")} ${colorFor(agg.mu, fmt(agg.mu))}` : "", slots[1]);
  out += padVisible(agg.p95 > 0 && fmt(agg.p95) !== fmt(agg.mu) ? ` ${dim("p95")} ${colorFor(agg.p95, fmt(agg.p95))}` : "", slots[2]);
  return padVisible(out, slots[0] + slots[1] + slots[2]);
}

function buildSections(theme: any, gpuIdx: number): { tg: string; pp: string } {
  const dim = (s: string) => theme.fg("dim", s);
  const st = state.stats?.gpus.find(g => g.gpu === gpuIdx);
  const tg = st?.tg ?? { hist: [], hist_w: 10, n: 0, mu: 0, p95: 0, display: 0, session_max: 1 };
  const pp = st?.pp ?? { hist: [], hist_w: 100, n: 0, mu: 0, p95: 0, display: 0, progress: 0, eta_s: null };
  const tgColor = (v: number, t: string) => speedColor(v, t, theme);
  const ppColor = (v: number, t: string) => perfBand(theme, t, v, 300, 500);

  // TG section: fixed-width slots for column alignment
  const tgSpeed = padVisible(speedText(tg.display, theme, tgColor), 9);
  const mtpStr = (tg.display > 0 && st?.mtp_valid && st.mtp > 0)
    ? ` (${perfBand(theme, `MTP ${st.mtp.toFixed(1)}%`, st.mtp, 50, 75)})` : "";
  const tgMtp = padVisible(mtpStr, 13);
  const tgHist = tg.hist.length ? histBars(theme, tg, tgColor) : dim(TRACK.repeat(8) + "70+");
  const tgStats = statsFor(theme, tg, tgColor, [6, 6, 8]);
  const tgSec = `${dim("[TG]")} ${gauge(tg.display, theme, tg.session_max)} ${tgSpeed} ${tgMtp} ${tgHist} ${tgStats}`;

  // PP section: progress slot fits "(99%, ETA 999 s)"; μ/p95 slots fit 5-digit cache-burst values.
  let prog = "";
  if (pp.progress > 0 && pp.display > 0) {
    let inner = `${Math.round(pp.progress * 100)}%`;
    if (pp.eta_s != null) inner += `, ETA ${Math.round(pp.eta_s)} s`;
    prog = ` (${inner})`;
  }
  const ppProg = padVisible(prog, 18);
  const ppSpeed = padVisible(speedText(pp.display, theme, ppColor), 9);
  const ppHist = pp.hist.length ? histBars(theme, pp, ppColor) : dim(TRACK.repeat(8) + "700+");
  const ppStats = statsFor(theme, pp, ppColor, [6, 8, 10]);
  const ppSec = `${dim("[PP]")} ${ppProg} ${ppSpeed} ${ppHist} ${ppStats}`;

  return { tg: tgSec, pp: ppSec };
}

function buildGpuLine(theme: any, alias: string, gpuIdx: number, width: number, maxTg: number, maxPp: number): string {
  const { tg, pp } = buildSections(theme, gpuIdx);
  const label = `${alias}: `;
  const minGap = 2;
  const used = visibleWidth(label) + maxTg + maxPp;
  const pad = Math.max(minGap, width - used);
  return label + padVisible(tg, maxTg) + " ".repeat(pad) + padVisible(pp, maxPp);
}

// ---- per-GPU hardware stats (column-aligned across all GPUs) ----
function buildHwStats(theme: any, gpu: Gpu, st?: StatsGpu): string[] {
  const dim = (s: string) => theme.fg("dim", s);
  const parts: string[] = [];
  const utilT = `util ${gpu.util.toFixed(0)}%`;
  parts.push(gpu.util <= 0 ? dim(utilT) : band(theme, utilT, gpu.util, 50, 80));
  const tempT = gpu.temp_max != null ? `${gpu.temp.toFixed(0)}\u00B0C (${gpu.temp_max.toFixed(0)}\u00B0C)` : `${gpu.temp.toFixed(0)}\u00B0C`;
  parts.push(band(theme, tempT, gpu.temp, 60, 80));
  // session means of chip and hotspot temps, collected server-side while the GPU was busy
  const tempMu = st?.temp_mu ?? 0;
  const hotMu = st?.hot_mu ?? 0;
  parts.push(tempMu > 0
    ? `${dim("\u03bcC")} ${band(theme, hotMu > 0 ? `${tempMu.toFixed(0)}\u00B0C (${hotMu.toFixed(0)}\u00B0C)` : `${tempMu.toFixed(0)}\u00B0C`, tempMu, 60, 80)}`
    : "\u00B7");
  const vramTempT = `vram ${gpu.vram_temp.toFixed(0)}\u00B0C`;
  parts.push(band(theme, vramTempT, gpu.vram_temp, 60, 80));
  const vramT = `${gb(gpu.vram_used)}/${gb(gpu.vram_total)}`;
  const vramPct = gpu.vram_total > 0 ? (100 * gpu.vram_used) / gpu.vram_total : 0;
  parts.push(band(theme, vramT, vramPct, 50, 80));
  const powerT = `${gpu.power.toFixed(0)}W`;
  parts.push(gpu.power <= 0 ? dim(powerT) : band(theme, powerT, gpu.power, 100, 200));
  // session mean power, collected server-side while the GPU was busy
  const pwrMu = st?.pwr_mu ?? 0;
  parts.push(pwrMu > 0
    ? `${dim("\u03BCW")} ${band(theme, pwrMu.toFixed(0) + "W", pwrMu, 100, 200)}`
    : "\u00B7");
  const fanT = `fan ${gpu.fan.toFixed(0)}%`;
  parts.push(gpu.fan <= 0 ? dim(fanT) : band(theme, fanT, gpu.fan, 50, 80));
  return parts;
}

function buildSysLine(theme: any): string {
  const dim = (s: string) => theme.fg("dim", s);
  const s = state.sys;
  if (!s) return dim("  ");
  const parts: string[] = [theme.fg("text", "CPU")];
  const utilT = `util ${s.cpu.toFixed(0)}%`;
  parts.push(s.cpu <= 0 ? dim(utilT) : band(theme, utilT, s.cpu, 50, 80));
  if (s.cpu_temp !== null && s.cpu_temp !== undefined) parts.push(band(theme, `${s.cpu_temp.toFixed(0)}°C`, s.cpu_temp, 60, 80));
  if (s.ram_total > 0) {
    const pct = (100 * s.ram_used) / s.ram_total;
    parts.push(band(theme, `RAM ${gb(s.ram_used)}/${gb(s.ram_total)}`, pct, 50, 80));
  }
  if (s.cpu_power !== null && s.cpu_power !== undefined) {
    const t = `${s.cpu_power.toFixed(0)}W`;
    parts.push(s.cpu_power <= 0 ? dim(t) : band(theme, t, s.cpu_power, 100, 200));
  }
  return parts.join("  ");
}

function buildLines(theme: any, width: number): string[] {
  const L: string[] = [];
  const dim = (s: string) => theme.fg("dim", s);
  const warn = (s: string) => theme.fg("warning", s);
  const now = Date.now();

  if (state.downSince > 0 && now - state.downSince >= OFFLINE_AFTER_MS) {
    L.push(warn("⚡ llama-metrics offline"));
    return L;
  }

  const gpus = state.gpu?.gpus ?? [];
  const sections = gpus.map((_, i) => buildSections(theme, i));
  const maxTg = Math.max(...sections.map(s => visibleWidth(s.tg)), 0);
  const maxPp = Math.max(...sections.map(s => visibleWidth(s.pp)), 0);
  const hwStats = gpus.map((g, i) => buildHwStats(theme, g, state.stats?.gpus.find(x => x.gpu === i)));
  const maxStatW = hwStats[0]?.map((_, j) =>
    Math.max(...hwStats.map(s => visibleWidth(s[j] ?? "")), 0)
  ) ?? [];
  for (let i = 0; i < gpus.length; i++) {
    if (i > 0) L.push("");
    const alias = gpus[i].alias || `GPU ${i}`;
    L.push(truncateToWidth(buildGpuLine(theme, alias, i, width, maxTg, maxPp), width));
    const label = `${alias}: `;
    const statsLine = hwStats[i].map((s, j) => padVisible(s, maxStatW[j])).join("  ");
    L.push(truncateToWidth(label + statsLine, width));
  }

  L.push("");
  const sysText = buildSysLine(theme);
  if (sysText) L.push(truncateToWidth(sysText, width));

  return L;
}

// ---- extension ----
export default function (pi: ExtensionAPI) {
  let theme: any;
  let ui: any;
  let tui: any;
  let timer: any = null;

  const requestRender = () => {
    if (tui && typeof tui.requestRender === "function") tui.requestRender();
  };

  let cachedLines: string[] | null = null;
  let cachedAt = 0;
  let cachedWidth = 0;

  const fetchFrom = async (base: string): Promise<void> => {
    const [perfRes, gpuRes, sysRes, statsRes] = await Promise.all([
      fetch(`${base}/perf`, { signal: AbortSignal.timeout(config.timeoutMs), headers: AUTH_HEADERS }),
      fetch(`${base}/gpu`, { signal: AbortSignal.timeout(config.timeoutMs), headers: AUTH_HEADERS }),
      fetch(`${base}/sys`, { signal: AbortSignal.timeout(config.timeoutMs), headers: AUTH_HEADERS }),
      fetch(`${base}/stats`, { signal: AbortSignal.timeout(config.timeoutMs), headers: AUTH_HEADERS }),
    ]);
    if (!perfRes.ok) throw new Error("HTTP " + perfRes.status);
    await perfRes.json();
    state.downSince = 0;
    if (gpuRes.ok && sysRes.ok) {
      const [gpuData, sysData] = await Promise.all([gpuRes.json(), sysRes.json()]);
      state.gpu = gpuData;
      state.sys = sysData;
    }
    if (statsRes.ok) state.stats = await statsRes.json();
  };

  const poll = async () => {
    if (state.busy) return;
    state.busy = true;
    let answered = false;
    try {
      // Try each configured endpoint in order until one answers.
      for (const base of ENDPOINTS) {
        try {
          await fetchFrom(base);
          answered = true;
          break;
        } catch {
          // this endpoint did not answer; try the next one
        }
      }
      if (!answered && !state.downSince) state.downSince = Date.now();
    } finally {
      state.busy = false;
      requestRender();
    }
  };

  pi.on("session_start", (_e, ctx) => {
    cachedLines = null;
    if (ctx.mode !== "tui") return;
    ui = ctx.ui;
    theme = ctx.ui.theme;
    ui.setWidget(
      "llama-status",
      (t: any, th: any) => {
        tui = t;
        theme = th;
        return {
          render(width: number): string[] {
            const now = Date.now();
            if (!cachedLines || width !== cachedWidth || now - cachedAt >= POLL_MS) {
              cachedLines = buildLines(theme, width);
              cachedAt = now;
              cachedWidth = width;
            }
            return cachedLines;
          },
          invalidate(): void {
            /* no cached state; TUI re-calls render */
          },
        };
      },
      { placement: "aboveEditor" },
    );
    timer = setInterval(poll, POLL_MS);
    poll();
  });

  pi.on("session_shutdown", () => {
    if (timer) clearInterval(timer);
    timer = null;
  });
}
