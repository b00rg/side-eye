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
4. Obstruction losses for bag and blocked walks are read from `nist/obstruction_losses.json`
   (committed, under 1 KB). To rebuild it from the raw data (see *Download NIST and VSC*):
   `python3 nist/obstruction_losses.py data/external/nist/BLE_RSSI_Data data/external/vsc nist/obstruction_losses.json`
   Each simulated bag or blocked walk draws one loss from that file. If the file is missing,
   `simulate_walks.py` falls back to the old constants (`BLOCK_DB` 6, `BAG_DB` 9) and says so.
5. Sanity mode without real walks: leave out `--real`, e.g.
   `python3 nist/simulate_walks.py data/external/mitre_rssi.csv nist/settings_example.json nist/sim_test --n 200`
   `python3 nist/compare_models.py --sim nist/sim_test --cutoff -68`

`sim_check.png` is a visual check of simulated walks.

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
- [ ] T21: NIST spread figure (RSSI spread per distance) for the slides.
- [ ] T16: use `balanced_group_folds()` from `pi/train_model.py` in `compare_models.py`.
- [ ] T17: replace `features()` in `compare_models.py` with `pi/canary_core.Tracker`.
- [ ] T18: `--thresh` sweep at 0.6 / 0.8 / 0.9.
- [ ] T22: set `--rate` from `packet_rate_hz` in the real walks.
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
