# Side Eye: TODO

Each task is one GitHub issue. **Needs** lists the tasks that must be finished first.
**A** = Lili: Pi, SD card, power, wiring, walker · **B** = Rachel: Android, calibration, training, NIST · **C** = Emma: Pi terminal, result log, floor marks, live graph, slides

## Can start right now (no dependencies)
T1 (A), T2 (B), T10 (C), T15 (C), T16–T19 (B), T29 (team), T30 (C)

## Pi setup
- [ ] **T1 · Write the SD card.** Raspberry Pi OS Lite 64-bit, hostname `canary`, user `canary`, iPhone hotspot Wi-Fi, SSH on. *Owner: A. Needs: none.*
- [ ] **T2 · Android advertiser.** nRF Connect, company ID `058E`, data `CA01`, non-connectable, low latency, no timeout. *Owner: B. Needs: none.*
- [ ] **T3 · Boot the Pi and SSH in.** `ssh canary@canary.local` works from the Mac. *Owner: A, C checks. Needs: T1.*
- [ ] **T4 · Install Pi dependencies.** `sudo apt install -y bluez python3-bleak python3-gpiozero python3-numpy python3-sklearn python3-joblib`; `bluetoothctl show` says Powered: yes. Add this line to `pi/README.md`. *Owner: C. Needs: T3.*
- [ ] **T5 · Put the repo on the Pi** at `/home/canary/side-eye` (repo is private: `scp -r` from the Mac, or clone with a token). This replaces the guide's Canary_code.zip step. Update `pi/README.md`. *Owner: C. Needs: T3.*
- [ ] **T6 · Run the Pi tests.** `cd pi && python3 -m unittest discover -s tests` ends with OK. *Owner: C. Needs: T4, T5.*
- [ ] **T7 · Scan check.** `python3 pi/canary.py scan --seconds 20` with the phone at 1 m: at least 20 callbacks over several seconds. *Owner: B. Needs: T2, T6.*
- [ ] **T8 · Wire the motor** with the Pi unplugged: pin 2 to VCC, pin 6 to GND, pin 12 (GPIO18) to IN. *Owner: A, C checks before power-on. Needs: T3.*
- [ ] **T9 · Motor test.** 3 × `python3 pi/canary.py motor-test` each give two buzzes; `vcgencmd get_throttled` prints `throttled=0x0`. *Owner: A. Needs: T6, T8.*

## Calibrate, record, train
- [ ] **T10 · Floor marks** at 1.5, 2, 4, 6 m in the final demo room. *Owner: C. Needs: none.*
- [ ] **T11 · Calibrate.** Pi fixed in its final spot; run `python3 pi/canary.py calibrate`. Log near median, far median and cutoff (gap of at least 6 dB). *Owner: B, A holds the phone. Needs: T7, T9, T10, T15.*
- [ ] **T12 · Record 24 walks:** 8 passer, 8 linger, 4 far, 2 bag, 2 blocked. `find data/runs -name '*.csv' | wc -l` prints 24. *Owner: A walks, C runs commands and tallies, B keeps advertising on. Needs: T11.*
- [ ] **T13 · Get the walks into the repo.** Copy `data/runs/` off the Pi and commit it. Log the `settings.json` values (the file itself is gitignored). *Owner: B. Needs: T12.*
- [ ] **T14 · Train.** `python3 pi/train_model.py`; log all 4 folds; `model.joblib` exists. *Owner: B. Needs: T12.*
- [ ] **T15 · Result log.** Add `docs/RESULT_LOG.md` (the guide refers to it, but it doesn't exist): scan counts, medians and cutoff, run tally, fold results, rehearsal trials. *Owner: C. Needs: none.*

## Model evaluation (B; code only, no Pi needed until T24)
- [ ] **T16 · Same folds.** `nist/compare_models.py` uses `balanced_group_folds()` from `pi/train_model.py`. *Owner: B. Needs: none.*
- [ ] **T17 · Same features.** Replace `features()` in `nist/compare_models.py` with `pi/canary_core.Tracker`. *Owner: B. Needs: none.*
- [ ] **T18 · Threshold sweep.** Add a `--thresh` option and report results at 0.6 / 0.8 / 0.9 (currently hard-coded to 0.8). *Owner: B. Needs: none.*
- [ ] **T24 · Rule vs model table** on the real walks: linger detected, false alarms, early alerts, plus the threshold trade-off. *Owner: B. Needs: T13, T14, T16, T18.*
- [ ] **T25 · Decide what drives the motor.** If the model beats the rule, add `live --use-model`; otherwise keep the rule and say so. *Owner: B, A tests on the Pi. Needs: T24.*

## Public data (B)
- [ ] **T19 · Download the NIST zip** into `data/external/`, read its README and confirm the file format. *Owner: B. Needs: none.*
- [ ] **T20 · Replace `BLOCK_DB` / `BAG_DB`** with the NIST person-in-between loss. *Owner: B. Needs: T19.*
- [ ] **T21 · NIST spread figure:** RSSI spread per distance (why one threshold isn't enough). *Owner: B. Needs: T19.*
- [ ] **T22 · Set `--rate`** to the median `packet_rate_hz` of the real walks. *Owner: B. Needs: T13.*
- [ ] **T23 · Simulate and compare.** About 300 walks with the real calibration; `compare_models.py --real data/runs` at `--sim-weight` 0.1 / 0.2 / 0.5; report all three. If NIST doesn't work out, skip this and keep only T21. *Owner: B. Needs: T13, T16, T17, T20, T22.*

## Demo
- [ ] **T26 · Live signal graph** on the laptop with the cutoff line, reading the Pi's output over SSH. Can be built against `live --no-motor` output; needs the real cutoff to finish. *Owner: C. Needs: T6 to start, T11 to finish.*
- [ ] **T27 · Rehearse 5 pass/stop trials** with `live`: silent pass, two-buzz stop, log stop-to-buzz time. *Owner: A walks, C logs. Needs: T9, T11, T14, T25.*
- [ ] **T28 · Autostart.** Install `pi/canary.service`, reboot, get one buzz without typing `live`. *Owner: A. Needs: T27.*
- [ ] **T33 · Final re-test** after the last power cycle, same room and marks. *Owner: A. Needs: T28.*

## Pitch and submission
- [ ] **T29 · Pick the name:** Side Eye or Canary, then use it everywhere (repo, CLI, slides). *Owner: team. Needs: none.*
- [ ] **T30 · Check the event rules:** number of tracks per project, real submission deadline. *Owner: C. Needs: none.*
- [ ] **T31 · Slides with real numbers:** T24 table, T23 result (or T21 figure only), live graph screenshot; say "phone simulator". *Owner: C. Needs: T21, T23, T24, T26, T29.*
- [ ] **T32 · Pitches:** core pitch plus one per track, each under 2 min, ending with "not yet validated on real glasses". *Owner: all. Needs: T30, T31.*
- [ ] **T34 · Submit and record a backup demo video.** *Owner: C. Needs: T32, T33.*

## Optional track add-ons (only once T28 works)
- [ ] **T35 · Tiger Data:** walks and NIST readings as time series; training and the graph read from it. Training data only, never live readings. *Owner: C. Needs: T13, T19, T28.*
- [ ] **T36 · Google Cloud:** nightly Cloud Run Job retrains from Tiger Data labels + walks (`pi/retrain.py`, `gcp/`); Pi pulls `model.joblib` with `gcp/fetch_model.sh`. Code done; project not deployed yet. *Owner: A. Needs: T23, T28.*

## Not doing
- ESP32 port and m2cgen C model: the Pi is the build.
- Solana: contradicts the privacy pitch.
- Real Meta glasses testing: not available; say so in every pitch.
- `scan --real` in the demo: the demo only accepts our test packet.
