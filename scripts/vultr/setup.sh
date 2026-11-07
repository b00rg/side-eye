#!/usr/bin/env bash
# One-time setup of a Vultr VM (Ubuntu 24.04) for Side Eye's heavy jobs: simulation and analysis.
# Run on the VM as a normal user with sudo:
#   GITHUB_TOKEN=<read-only token for b00rg/side-eye> bash setup.sh
# Afterwards put Tiger's connection string in ~/side-eye/.env as DATABASE_URL='postgres://...'.
set -euo pipefail
REPO_DIR="${REPO_DIR:-$HOME/side-eye}"

sudo apt-get update -y
sudo apt-get install -y python3-venv python3-pip git unzip curl

if [ ! -d "$REPO_DIR/.git" ]; then
  : "${GITHUB_TOKEN:?set GITHUB_TOKEN (the repo is private)}"
  git clone "https://x-access-token:${GITHUB_TOKEN}@github.com/b00rg/side-eye.git" "$REPO_DIR"
fi
cd "$REPO_DIR"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

# Public data (not in git): MITRE (~117 MB CSV) and NIST
PYTHON=.venv/bin/python scripts/get_mitre.sh
mkdir -p data/external/nist
curl -sSL -o data/external/nist/BLE_RSSI_Data.zip https://data.nist.gov/od/ds/mds2-2827/BLE_RSSI_Data.zip
unzip -q -o data/external/nist/BLE_RSSI_Data.zip -d data/external/nist -x '__MACOSX/*' '*.DS_Store'
touch .env
echo "Setup done. Add DATABASE_URL=... to $REPO_DIR/.env, then run scripts/vultr/train_from_tiger.sh"
