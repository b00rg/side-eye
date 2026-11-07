# pi/

Raspberry Pi 4 code, owned by teammates. They will commit it here:

- `canary.py`: BLE scanner, recorder (`canary.py record`), and motor control
- `canary_core.py`: sliding-window features (mean_rssi, var_rssi, slope, dwell_s, close_streak_s, packet_rate_hz) and the 3-second rule
- `train_model.py`: trains the 20-tree random forest on real walks (4-fold, held-out whole walks)
- `canary.service`: systemd unit to run the scanner at boot
- `tests/`: unit tests

This folder is empty until then.
