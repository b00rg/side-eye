#!/bin/sh
# Run ON the Pi (repo root): pull the latest retrained model from the bucket and restart live.
#   gcp/fetch_model.sh <bucket> [--force]
# Needs the gcloud CLI and the side-eye-pi key at $SIDE_EYE_GCP_KEY (default ~/side-eye-pi-key.json).
# Refuses a model trained with a different scikit-learn than the Pi's, unless --force.
set -eu
BUCKET=${1:?usage: gcp/fetch_model.sh <bucket> [--force]}
KEY=${SIDE_EYE_GCP_KEY:-$HOME/side-eye-pi-key.json}
cd "$(dirname "$0")/.."
# Own gcloud config dir, so this never touches any other gcloud login on the Pi.
export CLOUDSDK_CONFIG="$HOME/.config/side-eye-gcloud"
if ! gcloud auth list --format='value(account)' 2>/dev/null | grep -q side-eye-pi; then
    gcloud auth activate-service-account --key-file "$KEY" --quiet
fi
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

gcloud storage cp --quiet "gs://$BUCKET/models/latest.json" "gs://$BUCKET/model.joblib" "$TMP/"
SERVER=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['sklearn'])" "$TMP/latest.json")
LOCAL=$(python3 -c "import sklearn; print(sklearn.__version__)")
if [ "$SERVER" != "$LOCAL" ] && [ "${2:-}" != "--force" ]; then
    echo "Model built with scikit-learn $SERVER, Pi has $LOCAL: not installing (use --force," >&2
    echo "or redeploy with DEBIAN_RELEASE set to the Pi's release)." >&2
    exit 1
fi
python3 -c "import joblib,sys; m=joblib.load(sys.argv[1]); assert m.n_features_in_ == 4" "$TMP/model.joblib"
if [ -f model.joblib ]; then cp model.joblib model.joblib.prev; fi
mv "$TMP/model.joblib" model.joblib
cp "$TMP/latest.json" model.json
python3 -c "import json; i=json.load(open('model.json')); print('Installed', i['model'], '-', i['tiger_events'], 'Tiger events,', i['walks'], 'walks')"
if systemctl is-active --quiet canary 2>/dev/null; then sudo systemctl restart canary && echo "Restarted canary."; fi
