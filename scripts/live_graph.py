"""Live signal graph on the laptop: what the Pi sees, what Model 3 thinks, and when it buzzes.

Usage (from the repo root):
  python3 scripts/live_graph.py --ssh canary@canary.local          # the Pi's running canary.service
  python3 scripts/fake_live.py --stream | python3 scripts/live_graph.py --stdin   # rehearse, no Pi
  python3 scripts/live_graph.py --replay data/runs_session2/<walk>.csv ...  # recorded walks through Model 3
  add --demo for the audience view (one signal line; when Side Eye vs the old strength rule think someone lingers)
  add --snapshot out.png [--duration 90] to save a still image instead of opening a window

Top panel: signal strength (raw readings as dots, smoothed average as a line) against the near/far
cutoff. Bottom panel: the model's linger score against its alert line (Model 3 at 0.8, or the shape
model at shape_live's --alert, read from its start-up line). A solid BUZZ line marks
each motor alert; a dotted "rule" line marks where the old 3-second rule would have buzzed.

Reads the Pi's own `live` output lines, so nothing new runs on the Pi and nothing leaves the
local network. Over SSH it follows `journalctl -u canary.service -f`.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import deque

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINE = re.compile(r"RSSI\s+(-?\d+) dBm \| (?:avg\s+(-?[\d.]+) dBm \| )?near streak\s+([\d.]+) s \| "
                  r"ML linger score (\S+) \| (ALERT|watching)")
THRESH = re.compile(r"threshold (-?[\d.]+) dBm")
ALERT_AT = re.compile(r"score >= ([\d.]+) twice")   # shape_live.py's start-up line, e.g. --alert 0.7
SURFACE, INK, INK_2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#b5b4ae", "#e8e7e3"
SIGNAL, SCORE = "#2a78d6", "#eb6834"   # reference palette slots 1 and 2
RULE_C = "#1baf7a"                      # the 3-second rule, as in docs/figures (demo view)
RULE_COOLDOWN_S = 15


def parse(line):
    """One `canary.py live` line -> ("row", dict) | ("cutoff", float) | ("note", text) | None."""
    m = LINE.search(line)
    if m:
        raw, avg, streak, score, state = m.groups()
        return "row", dict(raw=int(raw), avg=float(avg) if avg else None, streak=float(streak),
                           score=None if score == "untrained" else float(score), alert=state == "ALERT")
    if "shape model" in line:
        m, c = ALERT_AT.search(line), THRESH.search(line)
        return "model", ("Shape model", float(m.group(1)) if m else 0.8, float(c.group(1)) if c else None)
    m = THRESH.search(line)
    if m:
        return "cutoff", float(m.group(1))
    if "Model updated" in line or line.startswith(("Feedback (", "Labelled last buzz", "Reported missed")):
        return "note", line.strip()
    return None


class Feed:
    """Collects parsed rows with timestamps; derives where the 3-second rule would have buzzed."""

    def __init__(self, window_s):
        self.rows, self.notes = deque(), deque()
        self.window_s, self.cutoff, self.t0 = window_s, None, None
        self.last_rule, self.lock = -1e9, threading.Lock()
        self.model_name, self.alert_level = "Model 3", 0.8   # replaced by shape_live's start-up line

    def add(self, line, now=None):
        parsed = parse(line)
        if not parsed:
            return
        now = time.monotonic() if now is None else now
        if self.t0 is None:
            self.t0 = now
        t = now - self.t0
        kind, value = parsed
        with self.lock:
            if kind == "model":
                self.model_name, self.alert_level, cutoff = value
                if cutoff is not None:
                    self.cutoff = cutoff
                return
            if kind == "cutoff":
                self.cutoff = value
            elif kind == "note":
                self.notes.append((t, value))
            else:
                value["t"] = t
                value["rule"] = value["streak"] >= 3.0 and t - self.last_rule >= RULE_COOLDOWN_S
                if value["rule"]:
                    self.last_rule = t
                self.rows.append(value)
            while self.rows and self.rows[0]["t"] < t - self.window_s:
                self.rows.popleft()
            while self.notes and self.notes[0][0] < t - self.window_s:
                self.notes.popleft()

    def snapshot(self):
        with self.lock:
            return list(self.rows), list(self.notes), self.cutoff


def read_stream(stream, feed, scale=1.0):
    """Lines from the Pi/stdin are timed on arrival (times `scale`, to undo a sped-up rehearsal);
    replayed walks arrive as (seconds, line) and keep their own clock."""
    start = time.monotonic()
    for item in stream:
        if isinstance(item, tuple):
            feed.add(item[1], item[0])
        else:
            feed.add(item if isinstance(item, str) else item.decode(errors="ignore"),
                     (time.monotonic() - start) * scale)


def replay_lines(paths, speed):
    """Turn recorded walks into `live`-style lines, scored by model.joblib, paced in real time."""
    import joblib
    import pandas as pd
    sys.path.insert(0, os.path.join(ROOT, "pi"))
    from canary_core import MODEL_COLUMNS, ModelAlert
    model = joblib.load(os.path.join(ROOT, "model.joblib"))
    cutoff = json.load(open(os.path.join(ROOT, "settings.json")))["threshold_dbm"]
    clock = 0.0
    yield clock, f"Side Eye live (replay); threshold {cutoff} dBm"
    for path in paths:
        d = pd.read_csv(path)
        scores = model.predict_proba(d[list(MODEL_COLUMNS)].values)[:, 1]
        alert, last = ModelAlert(), -1e9
        for (_, r), s in zip(d.iterrows(), scores):
            fire = alert.update(r.t, s) and r.t - last >= 15
            last = r.t if fire else last
            clock += 1.0
            yield clock, (f"RSSI {int(round(r.mean_rssi)):4d} dBm | avg {r.mean_rssi:6.1f} dBm | near streak "
                          f"{r.close_streak_s:4.1f} s | ML linger score {s:.2f} | {'ALERT' if fire else 'watching'}")
            time.sleep(1.0 / speed)
        clock += 8.0   # quiet seconds between walks
        time.sleep(8.0 / speed)


def draw(fig, axes, feed, window_s, title):
    rows, notes, cutoff = feed.snapshot()
    top, bottom = axes
    for ax in axes:
        ax.clear()
        ax.set_facecolor(SURFACE)
        ax.grid(axis="y", color=GRID, lw=1)
        ax.set_axisbelow(True)
        for side in ax.spines.values():
            side.set_visible(False)
        ax.tick_params(colors=INK_2, length=0)
    now = rows[-1]["t"] if rows else 0.0
    left = max(0.0, now - window_s)   # fill from the left, then scroll
    right = max(window_s, now) + 2

    def broken(values):
        """Break the line wherever readings stop for more than 2.5 s (no signal = no line)."""
        xs, ys, prev = [], [], None
        for r, v in zip(rows, values):
            if prev is not None and r["t"] - prev > 2.5:
                xs.append(float("nan"))
                ys.append(float("nan"))
            xs.append(r["t"])
            ys.append(float("nan") if v is None else v)
            prev = r["t"]
        return xs, ys
    t = [r["t"] for r in rows]

    if cutoff is not None:
        top.axhspan(cutoff, 0, color=SIGNAL, alpha=0.06, lw=0)
        label = (f"3-s rule cutoff ({cutoff:.0f} dBm)" if feed.model_name == "Shape model"
                 else f"near / far cutoff ({cutoff:.0f} dBm)")
        top.axhline(cutoff, color=INK_2, lw=1.5, ls=(0, (5, 4)), label=label)
    top.scatter(t, [r["raw"] for r in rows], s=10, color=MUTED, zorder=2, label="each reading")
    if any(r["avg"] is not None for r in rows):
        top.plot(*broken([r["avg"] for r in rows]), color=SIGNAL, lw=2, zorder=3, label="smoothed signal")
    top.set_ylim(-95, -2)   # close-up readings reach about -5 dBm
    top.set_ylabel("signal (dBm)", color=INK_2)
    top.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=3, frameon=False, fontsize=9, labelcolor=INK)

    if any(r["score"] is not None for r in rows):
        bottom.plot(*broken([r["score"] for r in rows]), color=SCORE, lw=2, label=f"{feed.model_name} linger score")
    bottom.axhline(feed.alert_level, color=INK_2, lw=1.5, ls=(0, (5, 4)),
                   label=f"alert level ({feed.alert_level:g})")
    bottom.legend(loc="lower left", bbox_to_anchor=(0, 1.0), ncol=2, frameon=False, fontsize=9, labelcolor=INK)
    bottom.set_ylim(-0.03, 1.08)
    bottom.set_ylabel("linger score", color=INK_2)
    bottom.set_xlabel("seconds", color=INK_2)

    for r in rows:
        if r["alert"]:
            for ax in axes:
                ax.axvline(r["t"], color=INK, lw=2.5, zorder=4)
            top.text(r["t"] + 0.5, -5, "BUZZ", color=INK, fontsize=11, fontweight="bold", va="top")
        if r["rule"]:
            for ax in axes:
                ax.axvline(r["t"], color=INK_2, lw=1.2, ls=":", zorder=3)
            top.text(r["t"] + 0.5, -93, "3-s rule would buzz", color=INK_2, fontsize=8, va="bottom")
    for nt, text in notes:
        label = ("glasses there" if "TP" in text else "false alarm" if "FP" in text
                 else "missed glasses" if text.startswith("Reported") else
                 "+feedback" if text.startswith("Feedback") else "model updated")
        bottom.text(nt - 0.6, 1.06, label, color=INK_2, fontsize=8, va="top", ha="right")
    for ax in axes:
        ax.set_xlim(left, right)
    state = "BUZZ" if rows and any(r["alert"] for r in rows[-3:]) else ("watching" if rows else "waiting for signal")
    fig.suptitle(f"{title}    {state}", x=0.08, y=0.995, ha="left", fontsize=14, fontweight="bold", color=INK)


def _runs(rows, on):
    """Contiguous stretches where on(row) is true -> [(start, width)], one second per row."""
    out, start, prev = [], None, None
    for r in rows:
        if on(r) and start is None:
            start = r["t"]
        elif not on(r) and start is not None:
            out.append((start, prev - start + 1))
            start = None
        prev = r["t"]
    if start is not None:
        out.append((start, prev - start + 1))
    return out


def draw_demo(fig, axes, feed, window_s, title):
    """Audience view: one signal line, and below it when each method thinks someone is lingering.

    Header: Side Eye's verdict (left) and the old 3-second strength rule's (right). Tracks: blue where the
    shape model is over its alert level (dot = buzz); green where the strength rule's near streak is >= 3 s.
    Colours match docs/figures (shape model blue, 3-second rule green); all text stays in ink.
    """
    rows, notes, _ = feed.snapshot()
    head, sig, tracks = axes
    for ax in axes:
        ax.clear()
        ax.set_facecolor(SURFACE)
        for side in ax.spines.values():
            side.set_visible(False)
        ax.tick_params(colors=INK_2, length=0, labelsize=11)
    head.axis("off")
    now = rows[-1]["t"] if rows else 0.0
    left, right = max(0.0, now - window_s), max(window_s, now) + 1
    level, last = feed.alert_level, (rows[-1] if rows else None)
    score = last["score"] if last else None
    buzzed = any(r["alert"] for r in rows if r["t"] >= now - 4)

    if last is None or score is None:
        verdict, active = "Waiting for signal", False
    elif buzzed:
        verdict, active = "Someone's lingering", True
    elif score >= level:
        verdict, active = "They're staying", True
    elif score >= 0.3:
        verdict, active = "Coming closer", False
    else:
        verdict, active = "Just passing", False
    rule_on = bool(last and last["streak"] >= 3.0)

    def label(x, ha, kicker, text, dot, size):
        head.text(x, 0.92, kicker, transform=head.transAxes, ha=ha, va="top", fontsize=10, color=INK_2,
                  fontweight="bold")
        t = head.text(x, 0.52, text, transform=head.transAxes, ha=ha, va="center", fontsize=size, color=INK)
        return t
    label(0.0, "left", "S I D E   E Y E", verdict, active, 26)
    if active:
        head.plot([-0.012], [0.52], "o", ms=11, color=SIGNAL, transform=head.transAxes, clip_on=False)
    if score is not None:
        head.text(0.0, 0.12, f"confidence {score:.0%}" + ("   ·   buzzed" if buzzed else ""),
                  transform=head.transAxes, fontsize=12, color=INK_2, va="center")
    label(1.0, "right", "S T R E N G T H   R U L E", "Would buzz" if rule_on else "Quiet", rule_on, 18)
    if rule_on:   # dot to the left of the right-aligned verdict, so it never touches the edge
        head.plot([0.80], [0.52], "o", ms=9, color=RULE_C, transform=head.transAxes, clip_on=False)
    recent = [n for n in notes if n[0] >= now - 8 and ("TP" in n[1] or "FP" in n[1] or n[1].startswith("Reported"))]
    if recent:
        text = recent[-1][1]
        head.text(1.0, 0.12, "labelled: glasses there" if "TP" in text else
                  "labelled: false alarm" if "FP" in text else "reported: missed glasses",
                  transform=head.transAxes, ha="right", fontsize=12, color=INK_2, va="center")

    t = [r["t"] for r in rows]
    avg = [r["avg"] if r["avg"] is not None else r["raw"] for r in rows]
    sig.grid(axis="y", color=GRID, lw=1)
    sig.set_axisbelow(True)
    if rows:
        lo, hi = min(avg) - 5, max(avg) + 5
        sig.plot(t, avg, color=INK_2, lw=2.2, solid_capstyle="round", zorder=3)
        sig.plot(t[-1:], avg[-1:], "o", ms=8, color=INK, mec=SURFACE, mew=2, zorder=4)
        sig.set_ylim(lo, hi)
        for r in rows:
            if r["alert"]:
                sig.axvline(r["t"], color=SIGNAL, lw=1.5, zorder=2)
                sig.text(r["t"] + 0.25, hi, "buzz", color=INK, fontsize=11, va="top")
    sig.set_yticklabels([])
    sig.set_xticks([])
    sig.text(0.0, 1.0, "Bluetooth signal  ·  higher is stronger", transform=sig.transAxes, fontsize=11,
             color=INK_2, va="bottom")

    for y, color, runs in ((1, SIGNAL, _runs(rows, lambda r: r["score"] is not None and r["score"] >= level)),
                           (0, RULE_C, _runs(rows, lambda r: r["streak"] >= 3.0))):
        tracks.plot([left, right], [y, y], color=GRID, lw=6, solid_capstyle="round", zorder=1)
        for start, width in runs:
            tracks.plot([start, start + width], [y, y], color=color, lw=6, solid_capstyle="round", zorder=2)
    for r in rows:
        if r["alert"]:
            tracks.plot([r["t"]], [1], "o", ms=12, color=SIGNAL, mec=SURFACE, mew=2.5, zorder=3)
    tracks.set_ylim(-0.6, 1.6)
    tracks.set_yticks([1, 0])
    tracks.set_yticklabels(["Side Eye", "Strength rule"], color=INK, fontsize=12)
    tracks.set_xticks([])
    tracks.set_xlabel(f"last {window_s:g} seconds  ·  bar = thinks someone is lingering  ·  dot = buzz",
                      color=INK_2, fontsize=11)
    for ax in (sig, tracks):
        ax.set_xlim(left, right)


def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--ssh", help="user@host of the Pi running canary.service")
    src.add_argument("--stdin", action="store_true")
    src.add_argument("--replay", nargs="+", help="recorded walk CSVs to play through model.joblib")
    ap.add_argument("--window", type=float, default=60, help="seconds shown")
    ap.add_argument("--speed", type=float, default=1.0,
                    help="replay speed; with --stdin, match fake_live.py --speed to keep real seconds")
    ap.add_argument("--demo", action="store_true",
                    help="audience view: plain-English status, one signal line, score vs the buzz line (30 s window)")
    ap.add_argument("--snapshot", help="save a still PNG after --duration seconds instead of a live window")
    ap.add_argument("--duration", type=float, default=60)
    args = ap.parse_args()

    import matplotlib
    if args.snapshot:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.animation import FuncAnimation

    if args.demo and args.window == 60:
        args.window = 30
    feed = Feed(args.window)
    painter = draw_demo if args.demo else draw
    if args.ssh:
        proc = subprocess.Popen(["ssh", args.ssh, "journalctl -u canary.service -f -n 20 -o cat"],
                                stdout=subprocess.PIPE, text=True)
        source, title = proc.stdout, f"Side Eye live: {args.ssh}"
    elif args.stdin:
        source, title = sys.stdin, "Side Eye live"
    else:
        source, title = replay_lines(args.replay, args.speed), "Side Eye replay"
    threading.Thread(target=read_stream, args=(source, feed, args.speed if args.stdin else 1.0), daemon=True).start()

    plt.rcParams.update({"font.family": "sans-serif", "font.size": 10})
    if args.demo:
        plt.rcParams.update({"font.family": ["Helvetica Neue", "Helvetica", "Arial", "sans-serif"]})
        fig, axes = plt.subplots(3, 1, figsize=(12.5, 7), gridspec_kw={"height_ratios": [1.15, 3, 0.85]})
    else:
        fig, axes = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True, gridspec_kw={"height_ratios": [3, 2]})
    fig.patch.set_facecolor(SURFACE)
    if args.snapshot:
        time.sleep(args.duration)
        painter(fig, axes, feed, args.window, title)
        fig.tight_layout()
        fig.savefig(args.snapshot, dpi=150, facecolor=SURFACE)
        rows = feed.snapshot()[0]
        print(f"Saved {args.snapshot}: {len(rows)} rows in view, {sum(r['alert'] for r in rows)} BUZZ, "
              f"{sum(r['rule'] for r in rows)} rule-would-buzz", flush=True)
        os._exit(0)
    _anim = FuncAnimation(fig, lambda _: painter(fig, axes, feed, args.window, title), interval=500, cache_frame_data=False)
    fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
