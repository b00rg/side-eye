# data/mock/: MOCK walks, not real data

24 fake walks in the Pi's exact CSV format, plus a mock `settings.json`, so the results
pipeline can be run and tested before the real walks exist. **Never report numbers from these.**

- Made by `python3 scripts/make_mock_runs.py data/external/mitre_rssi.csv data/mock`
  (simulated walks at 1.35 readings/s, the scan check's rate, run through the Pi's own
  `canary_core.Tracker`; same 8/8/4/2/2 mix and shuffled order as the run sheet).
- Mock calibration: 2 m = -48 dBm, 6 m = -58 dBm, cutoff -53 dBm.

Try the results-day pipeline on it:
```
python3 nist/simulate_walks.py data/external/mitre_rssi.csv data/mock/settings.json nist/sim --n 300 --rate 1.35
python3 nist/compare_models.py --sim nist/sim --real data/mock/runs --cutoff -53
```
When the real walks arrive: swap `data/mock/runs` for `data/runs` and `data/mock/settings.json`
for the real `settings.json` (and its `threshold_dbm` for `--cutoff`).
