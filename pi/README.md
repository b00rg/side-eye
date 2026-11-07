# pi/

Raspberry Pi 4 code (Emma Burgess, Lili Berenguer Jin). Run everything from the repo root.
All scripts read and write at the repo root: `settings.json`, `data/runs/`, `model.joblib`.

| File | What it does |
|------|--------------|
| `canary.py` | CLI: `scan`, `calibrate`, `record <scenario>`, `motor-test`, `live` |
| `canary_core.py` | BLE signature match and per-second features (`Tracker`): mean_rssi, var_rssi, slope, dwell_s, close_streak_s, packet_rate_hz |
| `train_model.py` | 20-tree random forest on `data/runs/`, 4 folds of held-out whole walks, saves `model.joblib` |
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

## Model 3, feedback button, cloud updates
```
python3 pi/train_model.py --sim-rows data/sim_rows.csv [--feedback feedback.db]   # Model 3 (+ button feedback)
python3 pi/canary.py live --use-model     # motor follows Model 3; falls back to the rule if no model yet
```
- `feedback.py`: the button (GPIO17 to GND, or `y` + Enter) saves anonymous training rows to
  `feedback.db`; `live` retrains every 20 new rows and reloads `model.joblib` by itself.
- `update_from_cloud.sh user@vultr-ip`: fetch new simulated rows from the Vultr VM and retrain here.
- The same files work in the repo (`pi/`) and flat on the Pi (`/home/canary/canary`); see
  `canary_core.project_root`.

## Tests
```
cd pi && python3 -m unittest discover -s tests
```

## Run at boot
Clone the repo to `/home/canary/side-eye` on the Pi, calibrate, then:
```
sudo cp pi/canary.service /etc/systemd/system/
sudo systemctl enable --now canary
```
