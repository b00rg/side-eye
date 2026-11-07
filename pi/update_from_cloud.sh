#!/usr/bin/env bash
# On the Pi: fetch the latest simulated rows from the Vultr VM and retrain Model 3 here.
# The Pi can reach the VM (outbound), but the VM can't reach the Pi (it's behind the hotspot),
# so the Pi pulls. Training happens on the Pi so the model matches the Pi's scikit-learn.
#   bash update_from_cloud.sh user@vultr-ip [remote-repo-dir]
# A running `canary.py live` picks up the new model.joblib by itself.
set -euo pipefail
HOST="${1:?usage: update_from_cloud.sh user@vultr-ip [remote-repo-dir]}"
REMOTE="${2:-side-eye}"
cd "$(dirname "$0")"
scp "$HOST:$REMOTE/data/sim_rows.csv" sim_rows.csv.new && mv sim_rows.csv.new sim_rows.csv
ARGS=(--sim-rows sim_rows.csv)
[ -f feedback.db ] && ARGS+=(--feedback feedback.db)
python3 train_model.py "${ARGS[@]}"
