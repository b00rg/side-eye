# pi/

Raspberry Pi 4 code (Emma Burgess, Lili Berenguer Jin). Run everything from the repo root.
All scripts read and write at the repo root: `settings.json`, `shape_model.joblib`, `model.joblib`.
Recorded walks live in `data/runs_session1/` and `data/runs_session2/` (see `data/README.md`).

| File | What it does |
|------|--------------|
| `canary.py` | CLI: `scan`, `calibrate`, `record <scenario>`, `motor-test`, `live` |
| `canary_core.py` | BLE signature match and per-second features (`Tracker`): mean_rssi, var_rssi, slope, dwell_s, close_streak_s, packet_rate_hz |
| `train_model.py` | 20-tree random forest on `data/runs/`, 4 folds of held-out whole walks, saves `model.joblib` |
| `shape_core.py`, `train_shape.py`, `shape_live.py` | **the shape model** (docs/RESULTS.md): live shape inputs, training, the live detector |
| `retrain_shape.py` | retrain the shape model with labelled alarms from Tiger Data (what the Google Cloud job runs, see `gcp/README.md`) |
| `retrain.py` | the same for the level model (`model.joblib`, `canary.py live --use-model`) |
| `alarm_store.py` | upload buzzes and button labels to Tiger Data (no MAC addresses) |
| `feedback.py` | older local button feedback (`train_model.py --feedback`); `live` now uses the wire button + Tiger instead |
| `canary.service` | systemd unit that runs `shape_live.py` at boot |
| `tests/` | unit tests |

## Usage
```
python3 pi/canary.py scan                 # check the test phone is seen
python3 pi/canary.py calibrate            # 2 m / 6 m medians -> settings.json
python3 pi/canary.py record linger        # one labelled walk -> data/runs/<time>-linger.csv
                                          # scenarios: passer, linger, far, bag, blocked
python3 pi/train_model.py                 # cross-validate and save model.joblib
python3 pi/canary.py motor-test           # pulse GPIO18
python3 pi/canary.py live [--no-motor]    # 3 s rule drives the motor; ML score is display-only
```

## The shape model, wire button, Tiger Data and the cloud
```
python3 pi/train_shape.py data/runs_session1 data/runs_session2   # -> shape_model.joblib
python3 pi/shape_live.py [--no-motor] [--alert 0.7 --cooldown 8]  # shape model drives the motor (demo: docs/DEMO.md)
```
`shape_live.py` uses the same wire button and Tiger upload as `canary.py live` (below). Every buzz
(`rule = shape-model`) and every missed-glasses report carries the four shape inputs at that second
(rise, drop, steady, held), so a labelled row is a training row for the shape model:
```
Pi: shape_live buzzes -> Tiger row (+ shape inputs) -> you touch/hold the wire button -> TP / FP / FN
Cloud: retrain_shape.py = walks + labelled Tiger rows, judged on held-out walks -> shape_model.joblib in the bucket
Pi: gcp/fetch_model.sh <bucket> -> shape_model.joblib; shape_live reloads it without a restart
```
`retrain_shape.py` only adds Tiger rows to the training side, compares walks-only vs walks + Tiger on
the same held-out walks (scored as `nist/shape_confirm.py`), and **writes nothing if the Tiger rows
make those walks worse** (`--force` overrides). Try the whole loop on a laptop, no hardware or
credentials: `python3 scripts/fake_live.py`.

`canary.py live --use-model` still runs the level model (`model.joblib`) and also reloads it when
`retrain.py` replaces it.

The same files work in the repo (`pi/`) and flat on the Pi (`/home/canary/canary`); see
`canary_core.project_root`. On a Pi whose `canary.py` predates the wire button, `shape_live.py` runs
as before with uploads and the button off.

## Tests
```
cd pi && python3 -m unittest discover -s tests          # everything offline, ~3 s
cd pi && SIDE_EYE_TIGER_TESTS=1 python3 -m unittest tests.test_tiger_integration -v   # real Tiger Data
cd pi && SIDE_EYE_DOCKER_TESTS=1 python3 -m unittest tests.test_gcp -v              # build the retrain image
cd pi && SIDE_EYE_GCP_TESTS=1 SIDE_EYE_GCP_PROJECT=side-eye-514022 SIDE_EYE_GCP_REGION=europe-west4 python3 -m unittest tests.test_gcp -v   # read-only, real project
```
| File | Covers |
|---|---|
| `test_canary_core.py` | signature matching, RSSI tracker |
| `test_train_model.py` | training smoke test |
| `test_alarm_store.py` | rows, privacy (no MAC, rotating refs), env loading, upload thread, offline buffer |
| `test_live_loop.py` | the real `live` loop in simulated time: 3 s rule, 15 s cooldown, button TP/FP/FN, flags, cleanup |
| `test_retrain.py` | Tiger events -> labelled rows, walks+Tiger training, model + metadata written |
| `test_retrain_shape.py` | shape retrain: Tiger shape rows, walk features match the live tracker, scoring as shape_confirm, refuses a worse model |
| `test_shape_live.py` | the real `shape_live` loop in simulated time: rise-and-hold buzzes with shape inputs uploaded, button TP/FP/FN, `--no-db` |
| `test_gcp.py` | `gcp/` with a fake `gcloud`: schedule off by default, secret via stdin only, private bucket, job wiring, `.gcloudignore` keeps secrets out, `fetch_model.sh` install/refuse/rollback. Opt-in: Docker build, read-only check of the real project |
| `test_schema.py` | code and `scripts/tiger_schema.sql` agree |
| `test_tiger_integration.py` | opt-in: schema, policies, CHECK rule, uploads, buffer replay, `annotate.py` (cleans up after itself) |

## Run at boot
Clone the repo to `/home/canary/side-eye` on the Pi, calibrate, then:
```
sudo cp pi/canary.service /etc/systemd/system/
sudo systemctl enable --now canary
```

## Alarm upload and annotation (Tiger Data)
`shape_live.py` and `canary.py live` upload **only alarms that fire** to the `events` table on Tiger Data, never raw
readings. Each row holds the time, device, a daily-rotating hash of the BLE address (the address
itself never leaves the Pi), the signature, and the signal features at that moment. A person then
labels each case:

| Buzzed? | Glasses there? | Outcome | How it gets in |
|---|---|---|---|
| yes | yes | TP | Pi uploads, `annotate.py tp` |
| yes | no  | FP | Pi uploads, `annotate.py fp` |
| no  | yes | FN | `annotate.py missed` |
| no  | no  | never stored | rejected by the database |

```
cp ../tiger-cloud-*-credentials.env .env    # or set SIDE_EYE_ENV=/path/to/file.env
pip install "psycopg[binary]"
python3 scripts/tiger_setup.py --smoke      # create tables (safe to re-run) + round-trip test
python3 pi/canary.py live                   # buzzes upload in the background (--no-db to stop)
python3 scripts/annotate.py pending         # buzzes waiting for a label
python3 scripts/annotate.py tp | fp [id]    # label the latest (or a given) buzz
python3 scripts/annotate.py missed --at "2026-11-07 14:03" --note "..."
python3 scripts/annotate.py report          # TP/FP/FN, precision, recall
```
### Wire button (two jumper wires, no real button needed)
- One wire on **GPIO17 (physical pin 11)**, one on **GND (physical pin 9)**. Touch the bare ends = press.
  The Pi's internal pull-up makes this safe. **Never touch the GPIO17 wire to a 3.3 V or 5 V pin.**
- Check it: `python3 pi/canary.py button-test` prints `tap` / `HOLD` with durations.
- In `shape_live.py` and `canary.py live` (needs Tiger Data on):

| You do | When | Means | Motor confirms |
|---|---|---|---|
| quick touch | within 20 s of a buzz | TP: glasses were there | 1 tick |
| hold ~2 s | within 20 s of a buzz | FP: false alarm | 2 ticks |
| quick touch | any other time | FN: glasses here, no buzz | 3 ticks |

A missed report includes the signal features of the strongest device the Pi is tracking at that
moment, so it can be used for training. `--no-button` turns the button off.

If the network is down, alarms wait in `data/alarm_buffer.jsonl` and are sent once it's back.
Everything is deleted after 30 days. Schema: `scripts/tiger_schema.sql`.
