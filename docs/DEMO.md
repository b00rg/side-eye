# Demo mode

What the live demo does, how it's set, and what it does **not** claim.

## Setting
Demo mode is the 3-second rule with a **close-contact cutoff**: the Pi buzzes when the stand-in
glasses (the UNO Q transmitter, `zeus`) stay **almost touching** the device for 3 seconds, and stays
quiet otherwise. On the Pi:
```
cat > ~/settings.json <<'EOF'
{ "threshold_dbm": -26.0, "near_median": -19.0, "far_median": -33.0 }
EOF
python3 canary.py live
```
Chosen from two 15 s scans in the demo room (8 Nov 2026), transmitter on:

| Position | Readings (dBm) | Typical |
|---|---|---|
| Touching the Pi (a few cm) | -5 to -31 (swings with antenna alignment) | mean ~-19, median -24 |
| About 1 m apart | -47 to -27 | median -33, never above -27 |

The cutoff (-26) sits above anything seen at 1 m; the 3-second requirement means one strong reading
or a brief lean-in doesn't trigger it. If touching doesn't trigger reliably, -28 is the weakest
safe value; if 1 m triggers, use -24.

## Faster, more insistent version: `pi/demo_live.py` (use this one)
With the -26 / -29 cutoffs the near streak kept resetting just short of 3 s while touching: about
half the touching readings sit at -28/-31, like 1 m. What only happens when touching is very strong
readings (-1 to -24); the strongest seen at 1 m was -27. `demo_live.py` buzzes when at least 3
readings stronger than -24 dBm arrive within 2 s, and repeats every 3 s while still close: three
0.4 s pulses each time. Replaying the logged readings: no buzz at ~1 m, first buzz ~1 s after touching.
```
python3 demo_live.py              # options: --strong -24 --count 3 --window 2 --cooldown 3
```

## Shape model live (`pi/shape_live.py`), the model-driven demo
Trained on the Pi on all 40 walks (`python3 train_shape.py ~/data/runs_session1 ~/data/runs`).
Buzzes when someone **approaches and stays**: the signal rises clearly and holds. Walk it like the
recordings: start at the 6 m mark or further, walk in, stop ~2 m away facing the Pi, stay ~15 s.
Small dips and returns (never really leaving) don't look like an arrival and won't trigger it.
The tested setting is `--alert 0.8`; for the demo `--alert 0.7` is more eager (a hand-chosen demo
setting, not the confirmed result). First live try (8 Nov): one clear 8 dB approach buzzed; a
5-6 dB approach peaked at 0.78 (would buzz at 0.7); a ~4 dB approach stayed low.

## This is a demo setting, not the experiment's result
- It was tuned in the demo room, by hand, after the experiment. It is **not** pre-registered and
  was not evaluated on held-out walks.
- The experiment (`docs/PREREGISTRATION.md`, session 1) asked whether a detector can tell
  **lingering at ~2 m** from walking past, standing at 4 m, a bag, or a body in the way. In a real
  room it could not: the signal barely changes between 2 m and 6 m, so no method beat "never
  buzz" (TC4TL nDCF >= 1.0 for all). Session 1 is reported in full.
- Demo mode sidesteps that by only reacting to **very close contact**, where the signal is
  unambiguous.

## Why the demo uses a rule, not a model: `docs/figures/concept.png`
Same methods, folds and score (TC4TL nDCF: misses + false alarms, lower is better, 1.0 = never
buzzing) in two worlds. Left, simulated walks where the signal falls off with distance: the models
beat the 3-second rule clearly (Model 1 0.12, Models 2-3 0.25, rule 1.50). Right, our real room
(session 1): nothing beats never buzzing (rule 1.88, models 1.00-1.25). The models work when the
signal carries distance; our hardware in a real room doesn't provide it, so the next step is
better sensing (second receiver, motion sensor), not a better model. Left-hand walks are simulated
for both training and testing, so they show the concept, not real-world performance. Rebuild with
`python3 scripts/concept_figure.py --out docs/figures/concept.png`. (Model 2's real-room bar uses
the current simulator, with far walks at 4 m; the pre-registered session 1 run gave Model 2 1.12.)

## Real-world check on public data: H0H1 (`nist/h0h1_eval.py`)
Does the model beat a simple threshold on **real** Bluetooth data that isn't ours? MIT Lincoln
Laboratory's H0H1 dataset (PACT, CC BY 4.0): people living together, iPhones in hands or pockets,
~15 min sessions in their homes, either within 6 ft ("close") or 10 ft or more apart ("far").
17 sessions (11 close, 6 far), 1,094 ten-second windows, grouped 5-fold cross-validation by session.

| | 10 s windows | 30 s windows |
|---|---|---|
| Best single threshold (chosen on training sessions) | 64% (95% CI 53-76%) | 63% |
| Random forest, all inputs | 53% (43-65%) | 58% |
| Random forest, without readings/s (exploratory) | 59% | 62% |
| Random forest, signal level only (exploratory) | 59% | 62% |

Balanced accuracy; 50% = coin flip. The model at best ties the threshold and never beats it, and
neither gets much past ~60-65%. This matches session 1 and NIST IR 8437: **Bluetooth signal
strength alone carries little distance information in real spaces**, so the limit is the sensor,
not the model. The simulated "models win" panel in `docs/figures/concept.png` only holds where the
signal tracks distance cleanly, which real rooms don't provide. Next steps are better sensing
(a second receiver, a motion sensor on the wearer, UWB), not a better model.

## Assumptions and limits (say these out loud)
- One room, one receiver (Raspberry Pi 4), one transmitter (UNO Q broadcasting Meta's company ID
  0x058E every 20 ms) standing in for real glasses. Not validated on real smart glasses.
- "Near" means almost touching. Anything in the grey zone of roughly 0.5-4 m is **not** reliably
  detected, and we know it.
- It detects a device that's switched on and broadcasting, not whether it's recording.
- The ML model is shown as a score on screen but does not drive the motor in demo mode.
