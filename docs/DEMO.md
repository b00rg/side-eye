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

## This is a demo setting, not the experiment's result
- It was tuned in the demo room, by hand, after the experiment. It is **not** pre-registered and
  was not evaluated on held-out walks.
- The experiment (`docs/PREREGISTRATION.md`, session 1) asked whether a detector can tell
  **lingering at ~2 m** from walking past, standing at 4 m, a bag, or a body in the way. In a real
  room it could not: the signal barely changes between 2 m and 6 m, so no method beat "never
  buzz" (TC4TL nDCF >= 1.0 for all). Session 1 is reported in full.
- Demo mode sidesteps that by only reacting to **very close contact**, where the signal is
  unambiguous.

## Assumptions and limits (say these out loud)
- One room, one receiver (Raspberry Pi 4), one transmitter (UNO Q broadcasting Meta's company ID
  0x058E every 20 ms) standing in for real glasses. Not validated on real smart glasses.
- "Near" means almost touching. Anything in the grey zone of roughly 0.5-4 m is **not** reliably
  detected, and we know it.
- It detects a device that's switched on and broadcasting, not whether it's recording.
- The ML model is shown as a score on screen but does not drive the motor in demo mode.
