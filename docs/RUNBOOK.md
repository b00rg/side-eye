# Runbook: from 24 walks to Model 3 on the Pi

Everything below has been run end to end on real walks (session 1 and the confirmation set).

## 0. Transmitter
Power the UNO Q (`zeus`) from a power bank; it broadcasts by itself after ~25 s
(`transmitter/README.md`). Check from the Pi: `python3 canary.py scan --seconds 20` should give
~150+ at 1 m. Turn off any phone running nRF Connect so only zeus broadcasts.

## 1. Record (on the Pi)
Calibrate with zeus worn at head height, then record the 24 walks in the run-sheet order
(see `docs/DECISIONS.md` for the walk rules). Then: `cp settings.json settings_room1.json`.

## 2. Bring the walks to the Mac (repo root)
```
mkdir -p data/runs_session3
scp 'canary@canary.local:~/data/runs/*.csv' data/runs_session3/
scp canary@canary.local:/home/canary/canary/settings.json settings.json
```
Use a new session folder each time, so sessions never mix.

## 3. Results (Mac, about a minute)
```
python3 scripts/run_results.py --runs data/runs_session3
```
Prints Lili's training printout, the Rule vs Models 1-3 table (walks correct /24 with 95% ranges),
the pre-registered tests, both sweeps, the fake-vs-real check, nDCF, ablations, and the **T25
decision** (`docs/PREREGISTRATION.md`, Amendment 1). Everything is saved in `results/`, including
`results/det_curve.png` and `results/log.txt`.

## 4. If T25 says Model 3: deploy it
```
python3 scripts/deploy_model3.py
```
Exports `data/sim_rows.csv`, checks that Model 3 trains, rebuilds `Canary_code.zip` (code, tests,
`sim_rows.csv`, a `canary.service` that runs `live --use-model`), and prints the Pi commands:
```
# Mac: unzip Canary_code.zip in ~/Downloads, then
cd ~/Downloads/Canary_code
scp canary*.py train_model.py feedback.py sim_rows.csv canary.service canary@canary.local:/home/canary/canary/
scp -r tests canary@canary.local:/home/canary/canary/
# Pi:
cd /home/canary/canary
python3 -m unittest discover -s tests            # OK
python3 train_model.py --sim-rows sim_rows.csv   # trains Model 3 on the Pi -> model.joblib
python3 canary.py live --use-model --no-motor    # check the screen first
python3 canary.py live --use-model               # with the motor
sudo cp canary.service /etc/systemd/system/canary.service
sudo systemctl daemon-reload && sudo systemctl restart canary.service
```
The Pi trains its own `model.joblib` because a model saved on the Mac may not load with the Pi's
scikit-learn version.

## 5. See what it does (Mac)
```
python3 scripts/replay_walk.py data/runs_session2/<a linger walk>.csv data/runs_session2/<a passer walk>.csv
```
Plays recorded walks through Model 3, laid out like the `live` screen, with the rule alongside.

## 6. Feedback button (on the Pi, while `live` runs)
Press the button (GPIO17 to GND), or type `y` + Enter, when someone is lingering. Rows go to
`feedback.db`; every 20 new rows the Pi retrains in the background and `live` prints
"Model updated". Rehearse without hardware: `python3 scripts/fake_live.py` on the Mac runs the
real `live` loop with a fake radio, clock and button.

## 6b. Live signal graph (laptop, for the demo)
```
python3 scripts/live_graph.py --ssh canary@canary.local      # follows canary.service on the Pi
```
Top: signal vs the near/far cutoff. Bottom: Model 3's score vs 0.8. Solid BUZZ line when the motor
fires; dotted line where the old 3-second rule would have. Rehearse without the Pi:
`python3 scripts/fake_live.py --stream | python3 scripts/live_graph.py --stdin`, or replay recorded
walks: `python3 scripts/live_graph.py --replay data/runs_session2/<walk>.csv ...`. Slide figure (a walk-past and
a linger side by side): `python3 scripts/plot_walk_pair.py`; `deploy_model3.py` also makes it as `results/walk_pair.png`.


## Demo note
Demo commands and settings: `docs/DEMO.md` (shape model: walk in from 6 m+, stop ~2 m, stay ~15 s).

## If T25 says keep the rule
Skip step 4. The Pi keeps `live` with the 3-second rule (the guide's `canary.service`), and the
pitch says the models didn't clear the pre-registered bar.
