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


DEMO_BG, DEMO_TRACK, DEMO_LID = "#fbf7f2", "#ede7df", "#e9dfd3"   # warm paper, empty track, eyelid


def _draw_eye(ax, look, lidded, active):
    """A little eye: look = 0 (ahead) .. 1 (full side-eye); lidded = half-closed lid; active = blue iris."""
    from matplotlib.patches import Circle, Ellipse, Rectangle

    ax.clear()
    ax.set_facecolor(DEMO_BG)
    ax.axis("off")
    ax.set_aspect("equal")
    ax.set_xlim(-1.35, 1.35)
    ax.set_ylim(-1.0, 1.0)
    white = Ellipse((0, 0), 2.3, 1.35, fc="white", ec=INK, lw=2.5, zorder=1)
    ax.add_patch(white)
    x = 0.62 * look
    for patch in (Circle((x, 0), 0.47, fc=SIGNAL if active else MUTED, zorder=2),
                  Circle((x, 0), 0.22, fc=INK, zorder=3),
                  Circle((x - 0.12, 0.13), 0.08, fc="white", zorder=4)):
        ax.add_patch(patch)
        patch.set_clip_path(white)
    if lidded:
        lid = Rectangle((-1.3, 0.12), 2.6, 1.0, fc=DEMO_LID, ec="none", zorder=5)
        ax.add_patch(lid)
        lid.set_clip_path(white)
        edge = ax.plot([-1.2, 1.2], [0.12, 0.12], color=INK, lw=2.5, zorder=6, solid_capstyle="round")[0]
        edge.set_clip_path(white)
    ax.add_patch(Ellipse((0, 0), 2.3, 1.35, fc="none", ec=INK, lw=2.5, zorder=7))


def _runs(rows, on):
    """Contiguous stretches where on(row) is true -> [(start, end)], one second per row."""
    out, start, prev = [], None, None
    for r in rows:
        if on(r) and start is None:
            start = r["t"]
        elif not on(r) and start is not None:
            out.append((start, prev + 1))
            start = None
        prev = r["t"]
    if start is not None:
        out.append((start, prev + 1))
    return out


def draw_demo(fig, axes, feed, window_s, title):
    """Audience view: a side-eyeing eye + verdict, one soft signal line, and when each method thinks
    someone is lingering (blue = Side Eye's shape model, green = the old 3-second strength rule)."""
    eye, head, sig, tracks = axes
    rows, notes, _ = feed.snapshot()
    for ax in (head, sig, tracks):
        ax.clear()
        ax.set_facecolor(DEMO_BG)
        for side in ax.spines.values():
            side.set_visible(False)
        ax.tick_params(colors=INK_2, length=0, labelsize=12)
    head.axis("off")
    now = rows[-1]["t"] if rows else 0.0
    left, right = max(0.0, now - window_s), max(window_s, now) + 1
    level, last = feed.alert_level, (rows[-1] if rows else None)
    score = last["score"] if last else None
    buzzed = any(r["alert"] for r in rows if r["t"] >= now - 4)
    if last is None or score is None:
        verdict, look, lidded, active = "waiting for glasses...", 0, False, False
    elif buzzed:
        verdict, look, lidded, active = "someone's lingering!", 1, True, True
    elif score >= level:
        verdict, look, lidded, active = "they're staying...", 1, True, True
    elif score >= 0.3:
        verdict, look, lidded, active = "hmm, coming closer", 0.6, False, False
    else:
        verdict, look, lidded, active = "just passing by", 0, False, False
    _draw_eye(eye, look, lidded, active)

    head.set_xlim(0, 1)
    head.set_ylim(0, 1)
    head.text(0.0, 0.62, verdict, fontsize=28, color=INK, va="center", fontweight="demibold")
    sub = f"{score:.0%} sure someone's staying" if score is not None else "listening for our transmitter"
    head.text(0.0, 0.2, sub, fontsize=13, color=INK_2, va="center")
    rule_on = bool(last and last["streak"] >= 3.0)
    head.text(1.0, 0.62, "old strength rule", fontsize=12, color=INK_2, va="center", ha="right")
    head.text(1.0, 0.2, "would buzz" if rule_on else "quiet", fontsize=16, color=INK, va="center", ha="right",
              fontweight="demibold" if rule_on else "normal")
    recent = [n for n in notes if n[0] >= now - 8 and ("TP" in n[1] or "FP" in n[1] or n[1].startswith("Reported"))]
    if recent:
        text = recent[-1][1]
        head.text(0.55, 0.2, "labelled: glasses there" if "TP" in text else
                  "labelled: false alarm" if "FP" in text else "reported: missed glasses",
                  fontsize=12, color=INK_2, va="center", ha="center")

    t = [r["t"] for r in rows]
    avg = [r["avg"] if r["avg"] is not None else r["raw"] for r in rows]
    if rows:
        lo, hi = min(avg) - 5, max(avg) + 6
        sig.fill_between(t, lo, avg, color=SIGNAL, alpha=0.07, lw=0)
        sig.plot(t, avg, color=INK_2, lw=3, solid_capstyle="round", solid_joinstyle="round")
        sig.plot(t[-1:], avg[-1:], "o", ms=10, color=INK, mec=DEMO_BG, mew=3, zorder=4)
        sig.set_ylim(lo, hi)
        for r in rows:
            if r["alert"]:
                sig.axvline(r["t"], color=SIGNAL, lw=2, alpha=0.8)
                sig.text(r["t"] + 0.3, hi - 0.5, "bzz!", color=INK, fontsize=13, va="top", fontweight="demibold")
    sig.set_xticks([])
    sig.set_yticks([])
    sig.set_xlim(left, right)
    sig.set_title("bluetooth signal  (higher = stronger)", loc="left", fontsize=12, color=INK_2, pad=6)

    tracks.set_xlim(left, right)
    tracks.set_ylim(-0.7, 1.7)
    for y, color, on in ((1, SIGNAL, lambda r: r["score"] is not None and r["score"] >= level),
                         (0, RULE_C, lambda r: r["streak"] >= 3.0)):
        tracks.plot([left + 0.3, right - 0.3], [y, y], color=DEMO_TRACK, lw=10, solid_capstyle="round")
        for start, end in _runs(rows, on):   # keep the rounded ends inside the panel
            tracks.plot([max(start, left + 0.3), min(end, right - 0.3)], [y, y], color=color, lw=10,
                        solid_capstyle="round")
    for r in rows:
        if r["alert"]:
            tracks.plot([r["t"]], [1], "o", ms=15, color=SIGNAL, mec=DEMO_BG, mew=3, zorder=3)
    tracks.set_yticks([1, 0])
    tracks.set_yticklabels(["side eye", "old rule"], color=INK, fontsize=13)
    tracks.set_xticks([])
    tracks.set_xlabel(f"last {window_s:g} s   ·   bar = thinks someone's lingering   ·   dot = buzz",
                      color=INK_2, fontsize=11)


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
        plt.rcParams.update({"font.family": ["Avenir Next", "Avenir", "Helvetica Neue", "sans-serif"]})
        fig = plt.figure(figsize=(11, 6.2), layout="constrained", facecolor=DEMO_BG)
        mosaic = fig.subplot_mosaic([["eye", "head"], ["sig", "sig"], ["tracks", "tracks"]],
                                    width_ratios=[1, 7], height_ratios=[1.1, 3, 0.95])
        axes = tuple(mosaic[k] for k in ("eye", "head", "sig", "tracks"))
    else:
        fig, axes = plt.subplots(2, 1, figsize=(11, 6.5), sharex=True, gridspec_kw={"height_ratios": [3, 2]})
    if not args.demo:
        fig.patch.set_facecolor(SURFACE)
    if args.snapshot:
        time.sleep(args.duration)
        painter(fig, axes, feed, args.window, title)
        if not args.demo:
            fig.tight_layout()
        fig.savefig(args.snapshot, dpi=150, facecolor=fig.get_facecolor())
        rows = feed.snapshot()[0]
        print(f"Saved {args.snapshot}: {len(rows)} rows in view, {sum(r['alert'] for r in rows)} BUZZ, "
              f"{sum(r['rule'] for r in rows)} rule-would-buzz", flush=True)
        os._exit(0)
    _anim = FuncAnimation(fig, lambda _: painter(fig, axes, feed, args.window, title), interval=500, cache_frame_data=False)
    if not args.demo:   # the demo figure uses constrained layout, which refits on every redraw and resize
        fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
