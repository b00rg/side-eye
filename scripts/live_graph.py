"""Live signal graph on the laptop: what the Pi sees, what Model 3 thinks, and when it buzzes.

Usage (from the repo root):
  python3 scripts/live_graph.py --ssh canary@canary.local          # the Pi's running canary.service
  python3 scripts/fake_live.py --stream | python3 scripts/live_graph.py --stdin   # rehearse, no Pi
  python3 scripts/live_graph.py --replay data/runs_session2/<walk>.csv ...  # recorded walks through Model 3
  add --demo for the audience view (old strength rule vs Side Eye, side by side, over one signal line)
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
RULE_RED = "#d03b3b"                    # the old rule firing (demo view)
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


def draw_demo(fig, axes, feed, window_s, title):
    """Audience view: the old strength rule and Side Eye judging the same signal, side by side.

    Left box: the 3-second strength rule (near streak >= 3 s means it would buzz). Right box: the shape
    model's verdict and score. Below: one smooth signal line, with Side Eye's buzzes (solid) and the
    moments the old rule would have buzzed (dotted).
    """
    from matplotlib.patches import FancyBboxPatch

    rows, notes, _ = feed.snapshot()
    banner, top = axes
    for ax in axes:
        ax.clear()
        ax.set_facecolor(SURFACE)
        for side in ax.spines.values():
            side.set_visible(False)
        ax.tick_params(colors=INK_2, length=0, labelsize=11)
    banner.axis("off")
    banner.set_xlim(0, 1)
    banner.set_ylim(0, 1)
    now = rows[-1]["t"] if rows else 0.0
    left, right = max(0.0, now - window_s), max(window_s, now) + 1
    level, last = feed.alert_level, (rows[-1] if rows else None)
    recent = [r for r in rows if r["t"] >= now - 4]

    # Old way: the 3-second strength rule
    if last is None:
        old, old_c = "no signal", INK_2
    elif last["streak"] >= 3.0:
        old, old_c = "WOULD BUZZ", RULE_RED
    else:
        old, old_c = "quiet", INK_2
    # Side Eye: the shape model
    score = last["score"] if last else None
    if any(r["alert"] for r in recent):
        new, new_c = "BUZZ!", SIGNAL
    elif last is None or score is None:
        new, new_c = "no signal", INK_2
    elif score >= level:
        new, new_c = "they're staying...", SIGNAL
    elif score >= 0.3:
        new, new_c = "coming closer...", INK_2
    else:
        new, new_c = "just passing", INK_2
    for x0, head, verdict, c, sub in (
            (0.0, "Old way: signal strength", old, old_c, "buzzes after 3 s of strong signal"),
            (0.52, "Side Eye: signal shape", new, new_c,
             f"sure someone's staying: {score:.0%}" if score is not None else "")):
        lit = c != INK_2
        banner.add_patch(FancyBboxPatch((x0, 0.04), 0.48, 0.9, boxstyle="round,pad=0,rounding_size=0.04",
                                        fc=c if lit else SURFACE, ec=c if lit else MUTED, lw=2,
                                        alpha=0.12 if lit else 1, transform=banner.transAxes))
        banner.add_patch(FancyBboxPatch((x0, 0.04), 0.48, 0.9, boxstyle="round,pad=0,rounding_size=0.04",
                                        fc="none", ec=c if lit else MUTED, lw=2, transform=banner.transAxes))
        banner.text(x0 + 0.24, 0.8, head, ha="center", va="center", fontsize=14, color=INK_2,
                    transform=banner.transAxes)
        banner.text(x0 + 0.24, 0.47, verdict, ha="center", va="center", fontsize=30, fontweight="bold", color=c,
                    transform=banner.transAxes)
        banner.text(x0 + 0.24, 0.16, sub, ha="center", va="center", fontsize=12, color=INK_2,
                    transform=banner.transAxes)

    t = [r["t"] for r in rows]
    avg = [r["avg"] if r["avg"] is not None else r["raw"] for r in rows]
    if rows:
        lo, hi = min(avg) - 4, max(avg) + 6
        top.plot(t, avg, color=INK, lw=4, solid_capstyle="round")
        top.fill_between(t, lo, avg, color=INK, alpha=0.06, lw=0)
        top.set_ylim(lo, hi)
        for r in rows:
            if r["rule"]:
                top.axvline(r["t"], color=RULE_RED, lw=2, ls=":")
                top.text(r["t"] + 0.3, lo + 0.5, "old way buzzes", color=RULE_RED, fontsize=12, va="bottom")
            if r["alert"]:
                top.axvline(r["t"], color=SIGNAL, lw=4)
                top.text(r["t"] + 0.3, hi - 0.5, "Side Eye buzzes", color=SIGNAL, fontsize=14,
                         fontweight="bold", va="top")
    for nt, text in notes:
        if "TP" in text or "FP" in text or text.startswith("Reported"):
            label = "labelled: glasses there" if "TP" in text else "labelled: false alarm" if "FP" in text \
                else "reported: missed"
            top.text(nt, top.get_ylim()[1], label, color=INK_2, fontsize=11, ha="center", va="bottom")
    top.set_yticks([])
    top.set_xticks([])
    top.set_xlim(left, right)
    top.set_xlabel(f"Bluetooth signal over the last {window_s:g} seconds   (higher = stronger)", color=INK_2,
                   fontsize=13)


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
        fig, axes = plt.subplots(2, 1, figsize=(12, 7), gridspec_kw={"height_ratios": [1.3, 3]})
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
