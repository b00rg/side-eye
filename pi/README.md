# pi/

Raspberry Pi 4 code (Emma Burgess, Lili Berenguer Jin). Run everything from the repo root.
All scripts read and write at the repo root: `settings.json`, `data/runs/`, `model.joblib`.

| File | What it does |
|------|--------------|
| `canary.py` | CLI: `scan`, `calibrate`, `record <scenario>`, `motor-test`, `live` |
| `canary_core.py` | BLE signature match and per-second features (`Tracker`): mean_rssi, var_rssi, slope, dwell_s, close_streak_s, packet_rate_hz |
| `train_model.py` | 20-tree random forest on `data/runs/`, 4 folds of held-out whole walks, saves `model.joblib` |
| `retrain.py` | retrain with labelled alarms from Tiger Data added to the walks (nightly on Google Cloud, see `gcp/README.md`) |
| `canary.service` | systemd unit that runs `canary.py live` at boot |
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
`canary.py live` uploads **only alarms that fire** to the `events` table on Tiger Data, never raw
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
- In `live` (needs Tiger Data on):

| You do | When | Means | Motor confirms |
|---|---|---|---|
| quick touch | within 20 s of a buzz | TP: glasses were there | 1 tick |
| hold ~2 s | within 20 s of a buzz | FP: false alarm | 2 ticks |
| quick touch | any other time | FN: glasses here, no buzz | 3 ticks |

A missed report includes the signal features of the strongest device the Pi is tracking at that
moment, so it can be used for training. `--no-button` turns the button off.

If the network is down, alarms wait in `data/alarm_buffer.jsonl` and are sent once it's back.
Everything is deleted after 30 days. Schema: `scripts/tiger_schema.sql`.
