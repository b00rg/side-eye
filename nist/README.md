# Canary public-data pipeline (Person B)

Run every command from the repo root.

1. `scripts/get_mitre.sh`
   Clones MITRE Range-Angle (Structured) into `data/external/` and runs
   `python3 nist/parse_mitre.py data/external/MITRE-Range-Angle-Structured data/external/mitre_rssi.csv`
   (106 Android/nRF Connect recordings, 3 to 15 ft). The CSV is not committed (>100 MB).
2. After Section 5 calibration, copy `settings.json` off the Pi (e.g. to `nist/settings.json`), then
   `python3 nist/simulate_walks.py data/external/mitre_rssi.csv nist/settings.json nist/sim --n 300 --rate <Pi packets/s>`
   Check the key names in settings.json match NEAR_KEYS / FAR_KEYS at the top of the script.
   `nist/settings_example.json` can stand in until then.
3. After Section 6 (24 real walks copied into `data/runs/`):
   `python3 nist/compare_models.py --sim nist/sim --real data/runs --cutoff <cutoff from settings.json>`
   Try `--sim-weight 0.1` and `0.5` too.
4. Sanity mode without real walks: leave out `--real`, e.g.
   `python3 nist/simulate_walks.py data/external/mitre_rssi.csv nist/settings_example.json nist/sim_test --n 200`
   `python3 nist/compare_models.py --sim nist/sim_test --cutoff -68`

Before trusting results, swap `features()` in compare_models.py for the feature code in
canary_core.py so simulated and real walks are featurised identically.

NIST obstruction data: BLOCK_DB and BAG_DB in simulate_walks.py are placeholders (6 and 9 dB).
Replace them with the person-in-between loss measured in the NIST zip
(data.nist.gov/od/ds/mds2-2827/BLE_RSSI_Data.zip).

`sim_check.png` is a visual check of simulated walks.

## TODO
- [ ] Replace `features()` in `compare_models.py` with the feature function from
      `pi/canary_core.py` once teammates commit it, so sim and real features match exactly.
- [ ] Check `settings.json` key names from the Pi match `NEAR_KEYS` / `FAR_KEYS`.
- [ ] Set `--rate` to the Pi's real packets per second (`packet_rate_hz` in real CSVs).
- [ ] Replace `BLOCK_DB` / `BAG_DB` with the person-in-between loss from the NIST zip.
- [ ] Try `--sim-weight 0.1`, `0.2`, `0.5` and report all.
