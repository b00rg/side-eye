#!/usr/bin/env bash
# Pull everything from Tiger, run the full pipeline, and publish sim_rows.csv for the Pi.
# Run on the Vultr VM (or any machine with the repo, public data and DATABASE_URL):
#   bash scripts/vultr/train_from_tiger.sh [--skip-ablations]
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -f .env ] && set -a && . ./.env && set +a
: "${DATABASE_URL:?set DATABASE_URL (in .env or the environment)}"
PY="${PYTHON:-.venv/bin/python}"

echo "== 1. Export walks, calibration, simulated rows and feedback from Tiger"
rm -f data/runs/*.csv data/runs/PLACEHOLDER
"$PY" scripts/tiger.py export .

echo "== 2. Full results pipeline"
"$PY" scripts/run_results.py "$@"

echo "== 3. Simulated rows for the Pi, from this run's simulation"
CUTOFF=$("$PY" -c "import json; print(json.load(open('settings.json'))['threshold_dbm'])")
"$PY" nist/export_sim_rows.py --sim nist/sim --cutoff "$CUTOFF" --out data/sim_rows.csv

echo "== 4. Store the new simulated rows back in Tiger"
"$PY" scripts/tiger.py load --sim-only && "$PY" scripts/tiger.py status

echo
echo "Done. The Pi fetches the new rows with: bash update_from_cloud.sh <vultr-user>@<vultr-ip>"
