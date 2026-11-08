#!/bin/sh
# Run ON the Pi (repo root): pull the latest retrained model from the bucket and restart live.
#   gcp/fetch_model.sh <bucket> [--force]               # the shape model (shape_live.py), from retrain_shape.py
#   MODEL=level gcp/fetch_model.sh <bucket> [--force]   # the level model (canary.py live), from retrain.py
# Needs the gcloud CLI and the side-eye-pi key at $SIDE_EYE_GCP_KEY (default ~/side-eye-pi-key.json).
# Refuses a model trained with a different scikit-learn than the Pi's, unless --force.
set -eu
BUCKET=${1:?usage: gcp/fetch_model.sh <bucket> [--force]}
MODEL=${MODEL:-shape}
case $MODEL in
    shape) FILE=shape_model.joblib; INFO=shape_latest.json; META=shape_model.json
           CHECK="assert m.n_features_in_ == 4 and tuple(getattr(m, 'feature_names_shape', ())) == ('rise', 'drop', 'steady', 'held')" ;;
    level) FILE=model.joblib; INFO=latest.json; META=model.json
           CHECK="assert m.n_features_in_ == 4 and not hasattr(m, 'feature_names_shape')" ;;
    *) echo "MODEL must be shape or level" >&2; exit 1 ;;
esac
KEY=${SIDE_EYE_GCP_KEY:-$HOME/side-eye-pi-key.json}
cd "$(dirname "$0")/.."
# Own gcloud config dir, so this never touches any other gcloud login on the Pi.
export CLOUDSDK_CONFIG="$HOME/.config/side-eye-gcloud"
if ! gcloud auth list --format='value(account)' 2>/dev/null | grep -q side-eye-pi; then
    gcloud auth activate-service-account --key-file "$KEY" --quiet
fi
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

gcloud storage cp --quiet "gs://$BUCKET/models/$INFO" "gs://$BUCKET/$FILE" "$TMP/"
SERVER=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['sklearn'])" "$TMP/$INFO")
LOCAL=$(python3 -c "import sklearn; print(sklearn.__version__)")
if [ "$SERVER" != "$LOCAL" ] && [ "${2:-}" != "--force" ]; then
    echo "Model built with scikit-learn $SERVER, Pi has $LOCAL: not installing (use --force," >&2
    echo "or redeploy with DEBIAN_RELEASE set to the Pi's release)." >&2
    exit 1
fi
python3 -c "import joblib,sys; m=joblib.load(sys.argv[1]); $CHECK" "$TMP/$FILE"
if [ -f "$FILE" ]; then cp "$FILE" "$FILE.prev"; fi
mv "$TMP/$FILE" "$FILE"
cp "$TMP/$INFO" "$META"
python3 -c "import json,sys; i=json.load(open(sys.argv[1])); print('Installed', i['model'], '-', i['tiger_events'], 'Tiger events,', i['walks'], 'walks')" "$META"
if systemctl is-active --quiet canary 2>/dev/null; then sudo systemctl restart canary && echo "Restarted canary."; fi
