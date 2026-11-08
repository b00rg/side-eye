# Results

Everything here follows `docs/PREREGISTRATION.md`. Exploratory analyses are marked as such.

## 1. Signal level can't judge distance in a real room (session 1, pre-registered)
24 real walks (8 linger, 8 passer, 4 far, 2 bag, 2 blocked), UNO Q transmitter at head height,
Raspberry Pi 4 receiver. No method beat "never buzz" (TC4TL nDCF >= 1.0 for all): the 3-second rule
buzzed at all 16 non-linger walks; Models 1-3 (signal-level inputs) caught 0-1 lingers. Cause: in this
room the signal barely changes between 2 m and 6 m (stopped linger ~-50 dBm, standing at 4 m ~-57,
heavily overlapping), and calibration read ~6 dB weaker than the walks. Details: Amendment 3.

The same limit shows on **public real-world data** (MIT-LL H0H1, 17 home sessions, close < 6 ft vs
far >= 10 ft): the best single threshold reached 64% balanced accuracy, our random forest 53-62%;
neither gets far past ~60-65% (`nist/h0h1_eval.py`, `docs/DEMO.md`). Consistent with NIST IR 8437.

## 2. Signal *shape over time* can (exploratory on session 1, then confirmed)

Session 2 (`data/runs_session2/`) is the 16 confirmation walks from Amendment 4; the session 2 planned
in Amendment 3 was abandoned before any of its walks were kept.
Instead of "how strong", the shape model asks "what is the signal doing": rise above its recent
low, drop below its recent high, steadiness, and seconds held up (`nist/shape_features.py`; past
rows only, as on the device). Same random forest, same alert rule (score >= 0.8 twice within 2.5 s).

**Exploratory (session 1, found after seeing the data):** 5/8 lingers, 0/16 false alarms, nDCF 0.38.
All 36 tried settings beat never buzzing (median nDCF 0.44).

**Confirmatory (Amendment 4, settings fixed beforehand, trained on all of session 1, tested once on
16 new walks):**

| | Lingers caught | False-alarm walks | Too early | nDCF |
|---|---|---|---|---|
| **Shape model** | **5/6** (95% CI 44-97%) | 3/10 | 1 | **0.47** |
| Level model (Model 1 inputs) | 2/6 (10-70%) | 0/10 | 0 | 0.67 |
| 3-second rule | 3/6 (19-81%) | 9/10 | 2 | 1.40 |
| Never buzz | 0/6 | 0/10 | 0 | 1.00 |

H4 primary **supported**: nDCF below 1.0 and below the 3-second rule's. Secondary: catches at least
half the lingers (pass, 5/6); false alarms at most 20% of other walks (**fail**, 3/10).

Per walk: all 4 far walks correct (a flat signal is never taken for a linger, which the rule got
wrong 9/10 times overall); 5/6 lingers (the miss alerted while the walker was still arriving); 3/6
walk-pasts falsely alerted, because at the peak of a walk-past the signal has just risen and briefly
looks like "arrived and stayed". With 6 lingers and 10 other walks the intervals are wide.

Reproduce: `python3 nist/shape_confirm.py --train data/runs_session1 --test data/runs_session2`.

## What this means
- Bluetooth signal strength alone can't tell *how close* someone is in a real room (our walks, MIT's
  homes, NIST's report agree).
- The *shape* of the signal over time does carry the behaviour we care about (approached and
  stayed vs passed vs stayed away), and a model can read it where a threshold can't.
- Next: walk-pasts are the weak spot. A longer look before deciding (e.g. requiring the signal to
  hold for longer) is the obvious fix, to be tested on new pre-registered walks, not tuned on these.
- Limits: one room, one transmitter standing in for real glasses, small samples, and detection of
  switched-on glasses rather than recording (`docs/DECISIONS.md`).
