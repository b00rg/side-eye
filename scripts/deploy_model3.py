"""Get Model 3 onto the Pi (after scripts/run_results.py says Model 3 drives the motor).

Usage (from the repo root):
  python3 scripts/deploy_model3.py

1. Exports the simulated walks from nist/sim as model rows: data/sim_rows.csv
2. Trains Model 3 here exactly as the Pi will (pi/train_model.py --sim-rows), as a check
3. Rebuilds Canary_code.zip from pi/ for the build guide's copy step, with sim_rows.csv and a
   canary.service that runs `live --use-model`
4. Prints the commands to run on the Pi. The Pi trains its own model.joblib, because a model
   saved with the Mac's scikit-learn may not load with the Pi's.
"""
import json
import os
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIM_ROWS = os.path.join(ROOT, "data", "sim_rows.csv")
ZIP = os.path.join(ROOT, "Canary_code.zip")
PI_FILES = ["canary.py", "canary_core.py", "train_model.py", "feedback.py", "update_from_cloud.sh"]
SERVICE = """[Unit]
Description=Side Eye smart-glasses detector (Model 3 drives the motor; falls back to the 3-second rule until model.joblib exists)
After=bluetooth.service
Wants=bluetooth.service

[Service]
Type=simple
User=canary
WorkingDirectory=/home/canary/canary
ExecStart=/usr/bin/python3 /home/canary/canary/canary.py live --use-model
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
"""


def step(title, args):
    print(f"\n== {title}", flush=True)
    subprocess.run([sys.executable, "-W", "ignore", *args], cwd=ROOT, check=True)


def main():
    cutoff = json.load(open(os.path.join(ROOT, "settings.json")))["threshold_dbm"]
    if not os.path.exists(os.path.join(ROOT, "nist", "sim", "manifest.csv")):
        sys.exit("No simulated walks in nist/sim. Run python3 scripts/run_results.py first.")
    step("1. Export simulated rows", ["nist/export_sim_rows.py", "--sim", "nist/sim", "--cutoff", str(cutoff),
                                      "--out", SIM_ROWS])
    step("2. Train Model 3 the way the Pi will (check)", ["pi/train_model.py", "--sim-rows", SIM_ROWS])

    step("2b. Slide figure with Model 3 (results/walk_pair.png)", ["scripts/plot_walk_pair.py"])

    print("\n== 3. Rebuild Canary_code.zip")
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for name in PI_FILES:
            z.write(os.path.join(ROOT, "pi", name), f"Canary_code/{name}")
        for name in sorted(os.listdir(os.path.join(ROOT, "pi", "tests"))):
            if name.endswith(".py"):
                z.write(os.path.join(ROOT, "pi", "tests", name), f"Canary_code/tests/{name}")
        z.writestr("Canary_code/canary.service", SERVICE)
        z.write(SIM_ROWS, "Canary_code/sim_rows.csv")
    print(f"Wrote {ZIP} ({os.path.getsize(ZIP) / 1e3:.0f} KB): " + ", ".join(
        n.split("/", 1)[1] for n in zipfile.ZipFile(ZIP).namelist()))

    print("""
== 4. On the Pi
From the Mac (unzip Canary_code.zip in ~/Downloads first; this replaces the code, keeps data/runs and settings.json):
  cd ~/Downloads/Canary_code
  scp canary*.py train_model.py feedback.py update_from_cloud.sh sim_rows.csv canary.service canary@canary.local:/home/canary/canary/
  scp -r tests canary@canary.local:/home/canary/canary/
On the Pi (ssh canary@canary.local):
  cd /home/canary/canary
  python3 -m unittest discover -s tests                 # should end with OK
  python3 train_model.py --sim-rows sim_rows.csv        # trains Model 3 -> model.joblib
  python3 canary.py live --use-model --no-motor         # check: score and ALERT lines, no buzzing
  python3 canary.py live --use-model                    # with the motor
To start it at boot instead of the 3-second rule:
  sudo cp canary.service /etc/systemd/system/canary.service
  sudo systemctl daemon-reload && sudo systemctl restart canary.service""")


if __name__ == "__main__":
    main()
