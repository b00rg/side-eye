# data/

## Real walks (committed)
Put the 24 real walk CSVs recorded on the Pi (`python3 pi/canary.py record <scenario>`) here:
8 linger, 8 passer, 4 far, 2 bag, 2 blocked.

- One CSV per walk, one row per second, with at least:
  `mean_rssi, var_rssi, slope, dwell_s, close_streak_s, label` (plus `packet_rate_hz`).
- The filename must contain the walk kind (`passer`, `linger`, `far`, `bag`, `blocked`),
  `canary.py` names them `<timestamp>-<scenario>.csv`. `nist/compare_models.py` uses this to build the folds.

Rule: **never commit recordings that contain anything sensitive** (other people's devices,
MAC addresses, locations). Ours only contain our own test phone's advertisement
(company ID 0x058E, data CA01), so they **can** be committed.

## Real walk folders
- `data/runs_session1/`: session 1, 24 walks + its `settings.json` (pre-registered results).
- `data/runs_session2/`: the 16 confirmation walks for the shape model (Amendment 4).
- New recordings: on the Pi, Lili's `canary.py` saves to `~/data/runs/`. Copy them into a new
  session folder:
```
mkdir -p data/runs_session3
scp 'canary@canary.local:~/data/runs/*.csv' data/runs_session3/
scp canary@canary.local:~/settings.json data/runs_session3/settings.json

## data/external/: public datasets (not committed)
Created by `scripts/get_mitre.sh`. Holds the cloned MITRE Range-Angle (Structured) repo
and the generated `mitre_rssi.csv` (~117 MB, over GitHub's 100 MB limit). The NIST
BLE RSSI zip should also go here once downloaded. Ignored by git.
