# data/

## data/runs/: real walks (committed)
Put the 24 real walk CSVs recorded on the Pi (`python3 pi/canary.py record <scenario>`) here:
8 linger, 8 passer, 4 far, 2 bag, 2 blocked.

- One CSV per walk, one row per second, with at least:
  `mean_rssi, var_rssi, slope, dwell_s, close_streak_s, label` (plus `packet_rate_hz`).
- The filename must contain the walk kind (`passer`, `linger`, `far`, `bag`, `blocked`),
  `canary.py` names them `<timestamp>-<scenario>.csv`. `nist/compare_models.py` uses this to build the folds.

Rule: **never commit recordings that contain anything sensitive** (other people's devices,
MAC addresses, locations). Ours only contain our own test phone's advertisement
(company ID 0x058E, data CA01), so they **can** be committed.

**Right now `data/runs/` holds 24 placeholder walks** (made by `scripts/make_placeholder_runs.py`,
marked by the empty `data/runs/PLACEHOLDER` file) and `settings.json` holds placeholder calibration,
so the pipeline can run before recording. To swap in the real walks, from the repo root:
```
rm data/runs/*.csv data/runs/PLACEHOLDER
scp canary@canary.local:/home/canary/canary/data/runs/*.csv data/runs/
scp canary@canary.local:/home/canary/canary/settings.json settings.json
```

## data/external/: public datasets (not committed)
Created by `scripts/get_mitre.sh`. Holds the cloned MITRE Range-Angle (Structured) repo
and the generated `mitre_rssi.csv` (~117 MB, over GitHub's 100 MB limit). The NIST
BLE RSSI zip should also go here once downloaded. Ignored by git.
