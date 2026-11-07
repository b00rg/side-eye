#!/usr/bin/env bash
# Clone MITRE Range-Angle (Structured) into data/external/ and build data/external/mitre_rssi.csv.
# Run from anywhere; paths are relative to the repo root. Set PYTHON to override the interpreter.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXT="$ROOT/data/external"
REPO="$EXT/MITRE-Range-Angle-Structured"
PYTHON="${PYTHON:-python3}"

mkdir -p "$EXT"
if [ -d "$REPO/.git" ]; then
  echo "MITRE repo already present at $REPO, skipping clone"
else
  git clone --depth 1 https://github.com/mkrangle/MITRE-Range-Angle-Structured.git "$REPO"
fi

"$PYTHON" "$ROOT/nist/parse_mitre.py" "$REPO" "$EXT/mitre_rssi.csv"
echo "Wrote $EXT/mitre_rssi.csv"
