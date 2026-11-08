"""Live demo view in the browser: what the Pi hears, and when Side Eye vs the old strength rule would buzz.

Usage (from the repo root):
  ssh canary@canary.local 'cd ~/side-eye && python3 -u pi/shape_live.py --alert 0.7 --cooldown 8' \\
      | .venv/bin/python scripts/live_view.py                      # the real Pi
  .venv/bin/python scripts/fake_live.py --stream | .venv/bin/python scripts/live_view.py   # rehearse, no Pi

Reads shape_live.py's (or canary.py live's) lines on stdin, serves a page on http://localhost:8765 and
opens it (add ?theme=light or ?theme=dark to force a theme). The page has fixed axes (signal -95..-5 dBm, last 30 s), so only the data moves. Nothing leaves
this laptop: the server listens on localhost only. Ctrl+C to stop.
"""
import argparse
import json
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_graph import parse  # noqa: E402  (same line format as the matplotlib graph)


class Events:
    """Every parsed line as a JSON event, kept for late-joining browsers (one page reload re-plays it)."""

    def __init__(self):
        self.items, self.cond, self.t0 = [], threading.Condition(), None

    def add(self, event):
        with self.cond:
            self.items.append(json.dumps(event))
            self.cond.notify_all()

    def since(self, i, timeout=15):
        with self.cond:
            if i >= len(self.items):
                self.cond.wait(timeout)
            return self.items[i:], len(self.items)


def read(stream, events):
    start = time.monotonic()
    for line in stream:
        parsed = parse(line)
        if not parsed:
            continue
        kind, value = parsed
        t = round(time.monotonic() - start, 2)
        if kind == "model":
            name, level, cutoff = value
            events.add({"type": "meta", "model": name, "level": level, "cutoff": cutoff})
        elif kind == "cutoff":
            events.add({"type": "meta", "cutoff": value})
        elif kind == "note":
            events.add({"type": "note", "t": t, "text": value})
        else:
            events.add({"type": "row", "t": t, "raw": value["raw"], "avg": value["avg"], "score": value["score"],
                        "alert": value["alert"], "rule": value["streak"] >= 3.0})
    events.add({"type": "end"})


def handler(events):
    page = PAGE.encode()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                i = 0
                try:
                    while True:
                        batch, i = events.since(i)
                        for item in batch:
                            self.wfile.write(f"data: {item}\n\n".encode())
                        if not batch:
                            self.wfile.write(b": keep-alive\n\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    return
            else:
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(page)

    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true", help="don't open the browser")
    args = ap.parse_args()
    events = Events()
    threading.Thread(target=read, args=(sys.stdin, events), daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(events))
    url = f"http://localhost:{args.port}"
    print(f"Side Eye live view: {url}  (Ctrl+C to stop)", file=sys.stderr, flush=True)
    if not args.no_open:
        threading.Timer(0.5, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Side Eye Live</title>
<style>
  :root {
    color-scheme: light;
    --surface: #fcfcfb; --panel: #ffffff; --border: #e8e7e3; --grid: #eeede9;
    --text: #0b0b0b; --text-2: #52514e; --text-3: #8f8e88;
    --side-eye: #2a78d6; --rule: #1baf7a; --signal: #52514e; --raw: #c9c8c2;
  }
  @media (prefers-color-scheme: dark) {
    :root:where(:not([data-theme="light"])) {
      color-scheme: dark;
      --surface: #1a1a19; --panel: #222220; --border: #34332f; --grid: #2c2b28;
      --text: #f0efec; --text-2: #c3c2b7; --text-3: #8f8e88;
      --side-eye: #3987e5; --rule: #199e70; --signal: #c3c2b7; --raw: #4a4945;
    }
  }
  :root[data-theme="dark"] {
    color-scheme: dark;
    --surface: #1a1a19; --panel: #222220; --border: #34332f; --grid: #2c2b28;
    --text: #f0efec; --text-2: #c3c2b7; --text-3: #8f8e88;
    --side-eye: #3987e5; --rule: #199e70; --signal: #c3c2b7; --raw: #4a4945;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; background: var(--surface); color: var(--text);
    font: 16px/1.4 "Avenir Next", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
  .app { height: 100%; display: grid; grid-template-rows: auto auto 1fr; gap: clamp(12px, 2vh, 24px);
    padding: clamp(16px, 3vh, 40px) clamp(16px, 4vw, 64px); max-width: 1600px; margin: 0 auto; }
  header { display: flex; align-items: baseline; justify-content: space-between; gap: 16px; }
  h1 { font-size: clamp(18px, 2vw, 24px); font-weight: 600; margin: 0; letter-spacing: -0.01em; }
  .live { font-size: 14px; color: var(--text-2); display: flex; align-items: center; gap: 8px; }
  .live i { width: 8px; height: 8px; border-radius: 50%; background: var(--text-3); }
  .live.on i { background: var(--text); animation: pulse 1.6s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: 0.25; } }
  .tiles { display: grid; grid-template-columns: 1fr 1fr; gap: clamp(12px, 2vw, 24px); }
  .tile { background: var(--panel); border: 1px solid var(--border); border-radius: 14px;
    padding: clamp(14px, 2vh, 22px) clamp(16px, 2vw, 26px); display: grid; gap: 4px; }
  .tile .k { font-size: 13px; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase; color: var(--text-2);
    display: flex; align-items: center; gap: 8px; }
  .tile .k b { width: 10px; height: 10px; border-radius: 3px; display: inline-block; }
  .tile .v { font-size: clamp(28px, 4vw, 48px); font-weight: 600; letter-spacing: -0.02em; line-height: 1.1; }
  .tile .s { font-size: 15px; color: var(--text-2); min-height: 1.4em; }
  .tile.hot { border-color: var(--accent); box-shadow: inset 0 0 0 1px var(--accent); }
  .charts { background: var(--panel); border: 1px solid var(--border); border-radius: 14px; min-height: 0;
    padding: clamp(14px, 2vh, 22px) clamp(16px, 2vw, 26px); display: grid; grid-template-rows: auto 1fr auto auto;
    gap: 6px; position: relative; }
  .ctitle { font-size: 14px; color: var(--text-2); display: flex; justify-content: space-between; gap: 12px; }
  .ctitle span:last-child { color: var(--text-3); }
  svg { width: 100%; height: 100%; display: block; overflow: visible; }
  #sig { min-height: 180px; }
  #lanes { height: 92px; }
  .axis text, .lane-label, .mark-label { font-size: 12px; fill: var(--text-2); }
  .lane-label { font-size: 14px; fill: var(--text); }
  .mark-label { font-weight: 600; fill: var(--text); }
  .tip { position: absolute; pointer-events: none; background: var(--panel); border: 1px solid var(--border);
    border-radius: 10px; padding: 8px 10px; font-size: 13px; box-shadow: 0 4px 16px rgb(0 0 0 / 0.08);
    display: none; white-space: nowrap; }
  .tip div { display: flex; justify-content: space-between; gap: 16px; color: var(--text-2); }
  .tip b { color: var(--text); font-weight: 600; }
  .legend { display: flex; gap: 20px; font-size: 13px; color: var(--text-2); flex-wrap: wrap; }
  .legend span { display: inline-flex; align-items: center; gap: 6px; }
  .legend i { width: 18px; height: 0; border-top: 2px solid; display: inline-block; }
  @media (max-width: 720px) { .tiles { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="app">
  <header>
    <h1>Side Eye &middot; live</h1>
    <div class="live" id="live"><i></i><span>waiting for the Pi</span></div>
  </header>
  <section class="tiles">
    <div class="tile" id="t-se" style="--accent: var(--side-eye)">
      <div class="k"><b style="background: var(--side-eye)"></b>Side Eye &middot; signal shape</div>
      <div class="v" id="se-v">Waiting</div>
      <div class="s" id="se-s"></div>
    </div>
    <div class="tile" id="t-rule" style="--accent: var(--rule)">
      <div class="k"><b style="background: var(--rule)"></b>Old rule &middot; signal strength</div>
      <div class="v" id="rule-v">Quiet</div>
      <div class="s" id="rule-s"></div>
    </div>
  </section>
  <section class="charts" id="charts">
    <div class="ctitle"><span>Bluetooth signal (dBm)</span><span>higher = stronger</span></div>
    <svg id="sig" role="img" aria-label="Bluetooth signal over the last 30 seconds"></svg>
    <svg id="lanes" role="img" aria-label="When each method thinks someone is lingering"></svg>
    <div class="legend">
      <span><i style="border-color: var(--signal)"></i>smoothed signal</span>
      <span><i style="border-color: var(--text-3); border-top-style: dashed"></i>old rule's cutoff</span>
      <span><i style="border-color: var(--side-eye); border-top-width: 6px; border-radius: 3px"></i>Side Eye thinks someone is lingering</span>
      <span><i style="border-color: var(--rule); border-top-width: 6px; border-radius: 3px"></i>old rule would buzz</span>
    </div>
    <div class="tip" id="tip"></div>
  </section>
</div>
<script>
const WINDOW = 30, YMIN = -95, YMAX = -5;
const theme = new URLSearchParams(location.search).get("theme");   // ?theme=light or ?theme=dark
if (theme === "light" || theme === "dark") document.documentElement.dataset.theme = theme;
const state = { rows: [], notes: [], level: 0.8, cutoff: null, live: false };
const $ = id => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
const el = (tag, attrs, parent) => { const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]); parent && parent.appendChild(e); return e; };
const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function size(svg) { const r = svg.getBoundingClientRect(); return [Math.max(r.width, 10), Math.max(r.height, 10)]; }
function clear(svg) { while (svg.firstChild) svg.removeChild(svg.firstChild); }
const PAD_L = 56, PAD_R = 16;

function now() { return state.rows.length ? state.rows[state.rows.length - 1].t : 0; }
function xScale(w) { const n = now(), lo = Math.max(0, n - WINDOW), hi = Math.max(WINDOW, n);
  return t => PAD_L + (t - (hi - WINDOW)) / WINDOW * (w - PAD_L - PAD_R); }

function runs(on) {   // contiguous stretches where on(row) holds: [[start, end]]
  const out = []; let start = null, prev = null;
  for (const r of state.rows) {
    if (on(r) && start === null) start = r.t;
    else if (!on(r) && start !== null) { out.push([start, prev + 1]); start = null; }
    prev = r.t;
  }
  if (start !== null) out.push([start, prev + 1]);
  return out;
}

function drawSignal() {
  const svg = $("sig"); clear(svg); const [w, h] = size(svg); const PB = 22;
  const x = xScale(w), y = v => 6 + (YMAX - v) / (YMAX - YMIN) * (h - 6 - PB);
  const g = el("g", { class: "axis" }, svg);
  for (let v = -90; v <= -10; v += 20) {
    el("line", { x1: PAD_L, x2: w - PAD_R, y1: y(v), y2: y(v), stroke: css("--grid") }, g);
    el("text", { x: PAD_L - 10, y: y(v) + 4, "text-anchor": "end" }, g).textContent = v;
  }
  const n = now(), lo = Math.max(WINDOW, n) - WINDOW;
  for (let s = 0; s <= WINDOW; s += 10) {
    const t = lo + s; el("text", { x: x(t), y: h - 4, "text-anchor": "middle" }, g).textContent =
      s === WINDOW ? "now" : `−${WINDOW - s} s`;
  }
  if (state.cutoff !== null) {
    el("line", { x1: PAD_L, x2: w - PAD_R, y1: y(state.cutoff), y2: y(state.cutoff), stroke: css("--text-3"),
      "stroke-dasharray": "5 5", "stroke-width": 1.5 }, svg);
  }
  const clip = el("clipPath", { id: "plot" }, el("defs", {}, svg));
  el("rect", { x: PAD_L, y: 0, width: w - PAD_L - PAD_R, height: h - PB }, clip);
  const plot = el("g", { "clip-path": "url(#plot)" }, svg);
  const rows = state.rows.filter(r => r.t >= lo - 1);
  for (const r of rows) if (r.raw !== null)
    el("circle", { cx: x(r.t), cy: y(Math.max(YMIN, Math.min(YMAX, r.raw))), r: 2.5, fill: css("--raw") }, plot);
  const pts = rows.filter(r => r.avg !== null).map(r => `${x(r.t)},${y(Math.max(YMIN, Math.min(YMAX, r.avg)))}`);
  if (pts.length > 1) el("polyline", { points: pts.join(" "), fill: "none", stroke: css("--signal"), "stroke-width": 2.5,
    "stroke-linejoin": "round", "stroke-linecap": "round" }, plot);
  for (const r of rows) if (r.alert) {
    if (r.t < lo) continue;
    el("line", { x1: x(r.t), x2: x(r.t), y1: 6, y2: h - PB, stroke: css("--side-eye"), "stroke-width": 2 }, svg);
    const label = el("text", { x: x(r.t) + 6, y: 18, class: "mark-label" }, svg); label.textContent = "buzz";
  }
  const last = rows[rows.length - 1];
  if (last && last.avg !== null) el("circle", { cx: x(last.t), cy: y(Math.max(YMIN, Math.min(YMAX, last.avg))), r: 5,
    fill: css("--signal"), stroke: css("--panel"), "stroke-width": 2 }, svg);
}

function drawLanes() {
  const svg = $("lanes"); clear(svg); const [w, h] = size(svg); const x = xScale(w);
  const n = now(), lo = Math.max(WINDOW, n) - WINDOW, hi = lo + WINDOW;
  const lanes = [["Side Eye", css("--side-eye"), r => r.score !== null && r.score >= state.level, h * 0.3],
                 ["Old rule", css("--rule"), r => r.rule, h * 0.75]];
  for (const [name, color, on, cy] of lanes) {
    el("text", { x: PAD_L - 10, y: cy + 5, "text-anchor": "end", class: "lane-label" }, svg).textContent = name;
    el("line", { x1: PAD_L, x2: w - PAD_R, y1: cy, y2: cy, stroke: css("--grid"), "stroke-width": 10,
      "stroke-linecap": "round" }, svg);
    for (const [a, b] of runs(on)) {
      if (b < lo || a > hi) continue;
      el("line", { x1: x(Math.max(a, lo)) + 5, x2: Math.max(x(Math.min(b, hi)) - 5, x(Math.max(a, lo)) + 5),
        y1: cy, y2: cy, stroke: color, "stroke-width": 10, "stroke-linecap": "round" }, svg);
    }
  }
  for (const r of state.rows) if (r.alert && r.t >= lo)
    el("circle", { cx: x(r.t), cy: h * 0.3, r: 8, fill: css("--side-eye"), stroke: css("--panel"), "stroke-width": 3 }, svg);
}

function verdicts() {
  const last = state.rows[state.rows.length - 1], n = now();
  const buzzed = state.rows.some(r => r.alert && r.t >= n - 4);
  let v = "Waiting", s = "listening for the transmitter", hot = false;
  if (last && last.score !== null) {
    const pct = Math.round(last.score * 100);
    if (buzzed || last.score >= state.level) { v = "Lingering"; hot = true; }
    else if (last.score >= 0.3) v = "Approaching";
    else v = "Passing by";
    s = `${pct}% confident someone is staying` + (buzzed ? " · buzzed" : "");
  }
  const note = state.notes.filter(x => x.t >= n - 8).pop();
  if (note) s = note.text.includes("TP") ? "labelled: glasses were there" : note.text.includes("FP") ?
    "labelled: false alarm" : note.text.startsWith("Reported") ? "reported: missed glasses" : s;
  $("se-v").textContent = v; $("se-s").textContent = s; $("t-se").classList.toggle("hot", hot);
  const ruleOn = !!(last && last.rule);
  $("rule-v").textContent = ruleOn ? "Would buzz" : "Quiet";
  $("rule-s").textContent = state.cutoff === null ? "" : ruleOn ?
    `signal above ${Math.round(state.cutoff)} dBm for 3 s` : `buzzes after 3 s above ${Math.round(state.cutoff)} dBm`;
  $("t-rule").classList.toggle("hot", ruleOn);
}

let pending = false;
function render() { if (pending) return; pending = true;
  requestAnimationFrame(() => { pending = false; drawSignal(); drawLanes(); verdicts(); }); }
new ResizeObserver(render).observe($("charts"));
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);

// hover: crosshair + tooltip with the reading at that moment
const charts = $("charts"), tip = $("tip");
charts.addEventListener("mousemove", ev => {
  const svg = $("sig"), r = svg.getBoundingClientRect(), [w] = size(svg);
  const n = now(), lo = Math.max(WINDOW, n) - WINDOW;
  const t = lo + (ev.clientX - r.left - PAD_L) / (w - PAD_L - PAD_R) * WINDOW;
  const row = state.rows.reduce((best, x) => !best || Math.abs(x.t - t) < Math.abs(best.t - t) ? x : best, null);
  if (!row || ev.clientX - r.left < PAD_L) { tip.style.display = "none"; return; }
  tip.innerHTML = `<div>${(row.t - n).toFixed(0)} s</div>` +
    `<div>signal <b>${row.avg !== null ? row.avg.toFixed(1) : row.raw} dBm</b></div>` +
    `<div>Side Eye confidence <b>${row.score !== null ? Math.round(row.score * 100) + "%" : "–"}</b></div>` +
    `<div>old rule <b>${row.rule ? "would buzz" : "quiet"}</b></div>` + (row.alert ? `<div><b>Side Eye buzzed</b></div>` : "");
  const box = charts.getBoundingClientRect();
  tip.style.display = "block";
  const left = Math.min(ev.clientX - box.left + 14, box.width - tip.offsetWidth - 8);
  tip.style.left = left + "px"; tip.style.top = (ev.clientY - box.top + 14) + "px";
});
charts.addEventListener("mouseleave", () => tip.style.display = "none");

const src = new EventSource("/events");
src.onmessage = m => {
  const e = JSON.parse(m.data);
  if (e.type === "row") { state.rows.push(e); const cut = e.t - 2 * WINDOW;
    while (state.rows.length && state.rows[0].t < cut) state.rows.shift(); }
  else if (e.type === "meta") { if (e.level !== undefined) state.level = e.level;
    if (e.cutoff !== undefined && e.cutoff !== null) state.cutoff = e.cutoff; }
  else if (e.type === "note") state.notes.push(e);
  else if (e.type === "end") { $("live").classList.remove("on"); $("live").lastChild.textContent = "stream ended"; }
  if (e.type === "row") { $("live").classList.add("on"); $("live").lastChild.textContent = "live"; }
  render();
};
src.onerror = () => { $("live").classList.remove("on"); $("live").lastChild.textContent = "reconnecting…"; };
</script>
</body>
</html>
"""

if __name__ == "__main__":
    main()
