# Public-data augmentation (Person B)

We only have 24 real walks, so we use public COVID contact-tracing Bluetooth data to
simulate thousands of realistic walks, then test whether that helps on our real held-out
walks. Four methods are compared on the same folds: Rule (3 s), Model 1 (real only),
Model 2 (sim only), Model 3 (sim + real, sim rows down-weighted).

This file is the running record of where the data stands. Tasks reference the
GitHub issues (T-numbers) in `TODO.md`.

## Pipeline
Run every command from the repo root.

1. `scripts/get_mitre.sh`
   Clones MITRE Range-Angle (Structured) into `data/external/` and runs
   `python3 nist/parse_mitre.py data/external/MITRE-Range-Angle-Structured data/external/mitre_rssi.csv`.
   The CSV is not committed (>100 MB).
2. After calibration (`python3 pi/canary.py calibrate`), `settings.json` is at the repo root
   (copy it off the Pi if you're working elsewhere), then
   `python3 nist/simulate_walks.py data/external/mitre_rssi.csv settings.json nist/sim --n 300 --rate <Pi packets/s>`
   `nist/settings_example.json` has the same format and can stand in until then.
3. After the 24 real walks are recorded into `data/runs/` (`python3 pi/canary.py record <scenario>`):
   `python3 nist/compare_models.py --sim nist/sim --real data/runs --cutoff <threshold_dbm from settings.json>`
   Prints the main table (rule waits 3 s, models alert at 0.8) plus two sweeps: the rule's wait
   (`--rule-waits`, default 1-6 s) and the models' score cutoff (`--thresholds`, default
   0.6,0.8,0.9). Saves `comparison_results.csv`, `rule_wait_sweep.csv`, `threshold_sweep.csv`.
   `stop_to_alert_s` is the median seconds from the stop to the alert for lingers caught in time.
4. Obstruction losses for bag and blocked walks are read from `nist/obstruction_losses.json`
   (committed, under 1 KB). To rebuild it from the raw data (see *Download NIST and VSC*):
   `python3 nist/obstruction_losses.py data/external/nist/BLE_RSSI_Data data/external/vsc nist/obstruction_losses.json`
   Each simulated bag or blocked walk draws one loss from that file. If the file is missing,
   `simulate_walks.py` falls back to the old constants (`BLOCK_DB` 6, `BAG_DB` 9) and says so.
5. Sanity mode without real walks: leave out `--real`, e.g.
   `python3 nist/simulate_walks.py data/external/mitre_rssi.csv nist/settings_example.json nist/sim_test --n 200`
   `python3 nist/compare_models.py --sim nist/sim_test --cutoff -68`

`sim_check.png` is a visual check of simulated walks.

**Slide figure (T21):** `nist/nist_spread.png`, rebuilt with
`python3 nist/plot_spread.py data/external/nist/BLE_RSSI_Data nist/nist_spread.png`.

## Data sources

| Dataset | What we use it for | Status | Get it |
|---|---|---|---|
| **MITRE Range-Angle (Structured)** ([PACT](https://mitll.github.io/PACT/datasets.html)) | Signal vs distance (path loss) and real second-to-second noise. Android transmitters only (nRF Connect, like our test phone); RSSI `127` dropped. | **In use.** 106 Android recordings, 3 to 15 ft, carried in hand or front pants pocket. | `scripts/get_mitre.sh` |
| **NIST BLE RSSI** ([mds2-2827](https://doi.org/10.18434/mds2-2827)) | Loss from a **person** blocking the path, for blocked walks. | **In use** (T20) via `obstruction_losses.json`. | See *Download NIST and VSC* below |
| **MIT-LL BLE RSSI Various Static Configurations** ([GitHub](https://github.com/mitll/BLE-RSSI-Various-Static-Configurations)) | Loss from a phone in a **purse**, for bag walks. | **In use** (T20) via `obstruction_losses.json`. | See below |
| NIST TC4TL Challenge ([site](https://www.nist.gov/itl/iad/mig/nist-tc4tl-challenge)) | Not data: the "too close for too long" framing, plus a scoring method (miss rate, false-alarm rate, nDCF) we can borrow for the results table. | Idea only. | — |

Other PACT datasets (H0H1, Range Angle, MITRE Unstructured, SocializeME, MIT Matrix,
anechoic chamber) were reviewed and skipped: they repeat what MITRE gives us, use
different hardware without distance labels, or were reserved as TC4TL test data.

### Download NIST and VSC
Both go in `data/external/` (gitignored).
```
mkdir -p data/external/nist
curl -L -o data/external/nist/BLE_RSSI_Data.zip https://data.nist.gov/od/ds/mds2-2827/BLE_RSSI_Data.zip
curl -L -o data/external/nist/2827_README.txt  https://data.nist.gov/od/ds/mds2-2827/2827_README.txt
unzip -q data/external/nist/BLE_RSSI_Data.zip -d data/external/nist -x '__MACOSX/*' '*.DS_Store'
git clone --depth 1 https://github.com/mitll/BLE-RSSI-Various-Static-Configurations.git data/external/vsc
```

## Findings so far

The tables below were first worked out by hand. The figures the simulation actually uses
come from `nist/obstruction_losses.py` (see *Used by the simulation*).

### NIST: person blocking the path (for `BLOCK_DB`)
- **Format:** 1,136 CSVs (`Timestamp_ms, ..., RSSI_dBm`), about 3 minutes each, two
  stationary devices 1.2 m off the ground, received by two Raspberry Pis (`RPi1`/`RPI2` folders).
- **Filename codes** (from Appendix A of NIST IR 8437; the zip's own README doesn't
  explain them): `CTData_<LOSn|NLOSn>_<sub>_O<orient>_<cm>cm_Device<d>.csv`.
  In LOS rooms 1 to 5: `sub0` = no one in the way, `sub1` = person 0.5 m from Device 1,
  `sub2` = person at the midpoint. `O1` = devices facing each other, `O2` = both facing up
  (person cases are O1 only). NLOS folders are walls, doors, corners and floors.
- **Loss vs clear path,** matched on room, device and distance:

  | Person position | All distances | At 2 m (our blocked walk) |
  |---|---|---|
  | 0.5 m from a device | **-10.0 dB** (IQR -14.8 to -7.2, n=150) | **-10.0 dB** (IQR -12.8 to -8.2) |
  | Midpoint | -4.0 dB (IQR -7.0 to -2.0, n=140) | -8.5 dB (IQR -10.0 to -6.5) |

  This agrees with the report's own figure for a person 0.5 m from a device:
  -11.04 dB median, -11.55 dB mean, SD 5.52 dB.
- The placeholder `BLOCK_DB = 6` underestimates the loss.

### VSC: phone in a purse (for `BAG_DB`)
- **Format:** iPhone receiver logs use the same line format as MITRE
  (`<time>,Bluetooth,<id>,<rssi>,<name>,...`). Each file holds 4 transmitters on one
  person at once: `TestPhone1` front pocket, `TestPhone2` purse (right side),
  `TestPhone3` back pocket, `TestPhone4` held to left ear. 6 ft apart, 8 body rotations,
  5 environments. 40 iPhone collects, about 112k readings.
- **Purse vs ear, same collect:** **-2.5 dB** median (IQR -7.0 to +3.2). The bag itself
  costs little.
- **Body rotation matters much more.** Purse side facing the receiver (270 to 315°) reads
  1 to 5 dB *stronger* than the ear; body in the way (135 to 180°) reads 5 to 10 dB weaker.
  Purse readings swing about 12 dB (-52 to -64 dBm) from rotation alone.
- **Effect on `BAG_DB = 9`:** that value is only realistic if a body is between the bag and
  the Pi. A bag on a chair with nothing in the way should be closer to 0 to 3 dB.
- **Caveat:** VSC has no in-hand baseline, so "ear" stands in for an unobstructed phone.

### NIST: why one threshold isn't enough (slide figure, `nist/nist_spread.png`)
- All clear-path readings from LOS rooms 1-5 (40,848 readings, 18 distances, 0.5 to 20 m).
- Medians: 2 m = -40 dBm, 6 m = -47 dBm, only 7 dB apart, while each distance's middle 80%
  spans about 20 to 30 dB.
- A single cutoff midway between them (-43.5 dBm, the same rule as `calibrate`) puts
  **37% of single 2 m readings on the "far" side and 37% of 6 m readings on the "near" side.**
- That's the case for using several seconds of signal (features over a window, the 3-second
  rule, the model) instead of any one reading.

### Why wait 3 seconds? (NIST can't pick the number; walking speed can)
- **Averaging longer barely improves the distance call in NIST.** Using NIST's 2 m and 6 m
  recordings, a per-room cutoff (like our calibration) and only setups that pass our 6 dB
  gate (8 of 16), the share of wrong near/far calls is 26% from one reading and still 24%
  after averaging 20 s. Across all rooms with one shared cutoff it's 37% and gets *worse*
  with longer windows, because each room sits at its own level.
- **Why:** NIST logged under 1 reading/s (3 s is about 2 readings), and most of its variation
  is slow (reflections, advertising channel), not second-to-second jitter. So the 3-second
  wait isn't there to make the distance estimate accurate.
- **The wait has to cover how long a walk-past stays on the "near" side of the cutoff.**
  Correction to an earlier version of this note, which assumed "near" ends at 2 m: calibration
  puts the cutoff midway (in dB) between the 2 m and 6 m medians, which is about **3.5 m**
  (the geometric mean, sqrt(2 x 6)). Walking 1.2 m/s with the closest approach at 1.5 m,
  someone is inside 3.5 m for about **5.2 s** (6.2 s at 1.0 m/s). So with a clean signal a 3 s
  rule buzzes at most walk-pasts, and at lingerers before they stop. On the working-case
  placeholder walks the rule only becomes good at 5-6 s. Whether 3 s holds up is for the
  rule-wait sweep on the real walks to show.
- **The real test is on our walks** (added to T18): rerun the rule with the wait set to
  1-6 s and count lingers caught, walk-pasts wrongly buzzed and time to buzz.

### Used by the simulation (`nist/obstruction_losses.json`)
| Walk | Source and method | Loss (dB to subtract) |
|---|---|---|
| blocked | NIST LOS1-5, devices facing each other, person 0.5 m from a device vs clear, matched on room, device and distance, 1-3 m only | median **12.5**, IQR 8.1 to 16.0, n=50 |
| bag | VSC iPhone logs, ear vs purse at **mirror rotations** (purse at 270° vs ear at 90°, etc.) so both are equally exposed; anechoic chamber excluded | median **3.8**, IQR 0.0 to 5.2, n=32 |

Mirror rotations stand in for "bag at hip height with nothing in the way" vs "phone at the
ear", which matches our bag-on-a-chair walk vs our ear-height walks. Comparing at the *same*
rotation (the -2.5 dB above) mixes in body shadowing, because ear and purse are on opposite sides.

## Decisions
Recorded in [`docs/DECISIONS.md`](../docs/DECISIONS.md): phone held at the right ear for
calibration and all walks; blocked walk with B 0.5 m in front of the Pi (`BLOCK_DB` about
-10 dB from NIST); bag walk kept as a hard case with the bag on a chair (`BAG_DB` about
0 to 3 dB from VSC).

## TODO
- [x] T19: download NIST, read README and report, confirm format.
- [x] T20: blocked and bag losses measured from NIST and VSC (`nist/obstruction_losses.py`),
      drawn per walk in `simulate_walks.py`; manifest records each walk's `loss_db`.
- [x] T21: NIST spread figure for the slides (`nist/plot_spread.py` -> `nist/nist_spread.png`).
- [x] T16: `compare_models.py` uses `balanced_group_folds()` from `pi/train_model.py`, so both
      scripts hold out the identical walks (checked on 24 fake Pi-format walks: all 4 folds match).
      It also builds its forest with `train_model.model_factory()` (same seed), so Model 1's
      scores are identical to what `train_model.py` produces.
- [x] T17: `compare_models.py` now runs simulated walks through `pi/canary_core.Tracker`, the
      Pi's own code, one snapshot per second like `canary.py record`. Before this, the old
      feature code disagreed with the Pi on whether the 3-second rule fires in 23% of seconds.
- [x] T18: sweeps of the models' cutoff (`--thresholds`) and the rule's wait (`--rule-waits`), with a stop-to-alert column.
- [ ] T22: set `--rate` from `packet_rate_hz` in the real walks. Decimals now work (e.g. `--rate 1.35`):
      a decimal rate gives a random (Poisson) number of readings per second, like the Pi's bursty
      callbacks. Scan check (T7) at ~1 m: 27 callbacks in 20 s = 1.35/s, 30% of seconds empty.
- [ ] T23: simulate about 300 walks and compare at `--sim-weight` 0.1 / 0.2 / 0.5.
- [ ] Optional: also report TC4TL-style miss rate, false-alarm rate and nDCF.
- [x] Check `settings.json` key names match `NEAR_KEYS` / `FAR_KEYS`
      (`calibrate` writes `near_median` / `far_median`).

## Citations
- MITRE Range-Angle (Structured), PACT, The MITRE Corporation.
- Moayeri N, Li C, Shi L (2022), *BLE RSSI Data for Proximity Detection in COVID-19
  Electronic Contact Tracing*, NIST, https://doi.org/10.18434/mds2-2827, and
  NIST IR 8437, https://doi.org/10.6028/NIST.IR.8437.
- MIT Lincoln Laboratory / MITRE, *BLE RSSI Various Static Configurations*, PACT, CC BY 4.0.
