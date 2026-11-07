# Side Eye

A wearable that detects smart glasses (e.g. Meta Ray-Bans) by listening for their Bluetooth Low Energy advertisements, and buzzes when one stays close for too long. Built at HackTheBurgh.

## How it works
- A **Raspberry Pi 4** scans BLE. For testing, an Android phone running **nRF Connect** advertises a stand-in packet (company ID `0x058E`, data `CA01`).
- Each second of signal becomes 4 features over sliding windows:
  - `mean_rssi`: smoothed RSSI, last 5 s
  - `var_rssi`: raw RSSI variance, last 5 s
  - `slope`: RSSI trend, last 5 s
  - `dwell_s`: seconds on the near side of a calibrated cutoff, last 10 s
  `close_streak_s` and `packet_rate_hz` are also logged.
- A **3-second rule** drives the vibration motor. A small **random forest** (20 trees) gives an ML "linger" score.
- **Labels:** in a linger walk, seconds after the first 3 s of the stop are 1; everything else is 0.

## Evaluation
24 real walks: 8 linger, 8 passer, 4 far, 2 bag, 2 blocked. Evaluated on held-out whole walks: 4 folds, each holding out 2 linger + 4 other walks.

Because 24 walks is very little data, we also simulate thousands of walks from public COVID contact-tracing Bluetooth data and compare four methods on the same folds:

| Method  | Trained on |
|---------|------------|
| Rule    | 3-second rule, no training |
| Model 1 | real walks only |
| Model 2 | simulated walks only |
| Model 3 | simulated + real, simulated rows down-weighted |

## Team
| Person | Part | Folder |
|--------|------|--------|
| Emma Burgess | Pi code: scanner, features, rule, model training, service, tests | `pi/` |
| Rachel Ranjith | Public-data augmentation (MITRE + NIST), model comparison | `nist/` |
| Lili Berengeur Jin | Pi code (with Emma Burgess) | `pi/` |

## Layout
```
pi/        Raspberry Pi code (teammates; see pi/README.md)
nist/      public-data simulation and model comparison
data/      real walks in data/runs/; public datasets in data/external/ (ignored)
scripts/   setup helpers
```

## Setup
```
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Running each part
**Pi (`pi/`):** see `pi/README.md` once the code is committed.

**Public-data augmentation (`nist/`)**, from the repo root:
```
scripts/get_mitre.sh                       # clone MITRE, build data/external/mitre_rssi.csv
python3 nist/simulate_walks.py data/external/mitre_rssi.csv nist/settings.json nist/sim --n 300 --rate <Pi packets/s>
python3 nist/compare_models.py --sim nist/sim --real data/runs --cutoff <cutoff>
```
Details and TODOs in `nist/README.md`.

## Data sources
- **MITRE Range-Angle (Structured)**: github.com/mkrangle/MITRE-Range-Angle-Structured.
  Only recordings with an Android transmitter (`partner_tester` in `Index.xlsx`) are used,
  since those used nRF Connect like our test glasses. RSSI `127` is an invalid sentinel and is dropped.
- **NIST BLE RSSI dataset**: data.nist.gov/od/ds/mds2-2827/BLE_RSSI_Data.zip (person-in-between
  and wall obstruction). Not integrated yet.
