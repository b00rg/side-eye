#!/bin/sh
# Run FROM the Mac (repo root) with the gcloud CLI logged in. Safe to re-run: creates what's missing,
# rebuilds the image with the current pi/ and data/runs/, and updates the job.
#   gcp/deploy.sh <project-id>
# Optional environment:
#   REGION=europe-west2        DEBIAN_RELEASE=bookworm (match the Pi: cat /etc/os-release)
#   BUCKET=<project>-side-eye-models
#   RETRAIN_ARGS=--tiger-weight,0.5,--no-demo   (comma-separated, passed to pi/retrain.py)
#   SCHEDULE=off (default: nightly retrain paused, no compute used) | on (retrain at 03:00 UTC)
set -eu
PROJECT=${1:?usage: gcp/deploy.sh <project-id>}
REGION=${REGION:-europe-west2}
DEBIAN_RELEASE=${DEBIAN_RELEASE:-bookworm}
BUCKET=${BUCKET:-$PROJECT-side-eye-models}
SCHEDULE=${SCHEDULE:-off}
case $SCHEDULE in on|off) ;; *) echo "SCHEDULE must be on or off" >&2; exit 1 ;; esac
JOB=side-eye-retrain
SECRET=side-eye-tiger-url
REPO=side-eye
JOB_SA=side-eye-retrain@$PROJECT.iam.gserviceaccount.com
PI_SA=side-eye-pi@$PROJECT.iam.gserviceaccount.com
IMAGE=$REGION-docker.pkg.dev/$PROJECT/$REPO/retrain:latest
cd "$(dirname "$0")/.."

URL=$(sed -n 's/^TIMESCALE_SERVICE_URL=//p' .env 2>/dev/null | tr -d "'\"")
[ -n "$URL" ] || { echo "No TIMESCALE_SERVICE_URL in .env (repo root)." >&2; exit 1; }
g() { gcloud --project "$PROJECT" --quiet "$@"; }
exists() { g "$@" >/dev/null 2>&1; }

echo "== APIs"
g services enable run.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com \
    artifactregistry.googleapis.com cloudbuild.googleapis.com storage.googleapis.com

echo "== Service accounts"
exists iam service-accounts describe "$JOB_SA" || g iam service-accounts create side-eye-retrain \
    --display-name "Side Eye nightly retrain job"
exists iam service-accounts describe "$PI_SA" || g iam service-accounts create side-eye-pi \
    --display-name "Side Eye Pi: read the model bucket"

echo "== Bucket gs://$BUCKET (private, versioned, old models deleted after 30 days)"
exists storage buckets describe "gs://$BUCKET" || g storage buckets create "gs://$BUCKET" \
    --location "$REGION" --uniform-bucket-level-access --public-access-prevention
g storage buckets update "gs://$BUCKET" --versioning --lifecycle-file gcp/lifecycle.json
g storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$JOB_SA" \
    --role roles/storage.objectUser >/dev/null
g storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$PI_SA" \
    --role roles/storage.objectViewer >/dev/null

echo "== Secret $SECRET (Tiger Data connection string)"
if ! exists secrets describe "$SECRET"; then
    printf %s "$URL" | g secrets create "$SECRET" --replication-policy automatic --data-file=-
elif [ "$(g secrets versions access latest --secret "$SECRET")" != "$URL" ]; then
    printf %s "$URL" | g secrets versions add "$SECRET" --data-file=-
fi
g secrets add-iam-policy-binding "$SECRET" --member "serviceAccount:$JOB_SA" \
    --role roles/secretmanager.secretAccessor >/dev/null

echo "== Image ($DEBIAN_RELEASE)"
# Builds run as the Compute Engine default service account, which new projects may leave with no
# roles: give it what a build needs (read the uploaded source, push the image, write logs).
NUMBER=$(g projects describe "$PROJECT" --format 'value(projectNumber)')
g projects add-iam-policy-binding "$PROJECT" \
    --member "serviceAccount:$NUMBER-compute@developer.gserviceaccount.com" \
    --role roles/cloudbuild.builds.builder --condition None >/dev/null
exists artifacts repositories describe "$REPO" --location "$REGION" || g artifacts repositories create "$REPO" \
    --location "$REGION" --repository-format docker
g builds submit --region "$REGION" --config gcp/cloudbuild.yaml \
    --substitutions "_IMAGE=$IMAGE,_DEBIAN_RELEASE=$DEBIAN_RELEASE" .

echo "== Cloud Run job $JOB"
set -- --image "$IMAGE" --region "$REGION" --service-account "$JOB_SA" \
    --set-secrets "TIMESCALE_SERVICE_URL=$SECRET:latest" \
    --add-volume "name=models,type=cloud-storage,bucket=$BUCKET" \
    --add-volume-mount "volume=models,mount-path=/models" \
    --execution-environment gen2 --memory 1Gi --cpu 1 --task-timeout 10m --max-retries 1
if [ -n "${RETRAIN_ARGS:-}" ]; then set -- "$@" --args "$RETRAIN_ARGS"; fi
if exists run jobs describe "$JOB" --region "$REGION"; then
    # Volumes are already on the job; re-adding them fails, so update only the rest.
    g run jobs update "$JOB" --image "$IMAGE" --region "$REGION" \
        --set-secrets "TIMESCALE_SERVICE_URL=$SECRET:latest" ${RETRAIN_ARGS:+--args "$RETRAIN_ARGS"}
else
    g run jobs create "$JOB" "$@"
fi
g run jobs add-iam-policy-binding "$JOB" --region "$REGION" --member "serviceAccount:$JOB_SA" \
    --role roles/run.invoker >/dev/null

echo "== Nightly schedule (03:00 UTC): $SCHEDULE"
RUN_URI=https://run.googleapis.com/v2/projects/$PROJECT/locations/$REGION/jobs/$JOB:run
if exists scheduler jobs describe "$JOB-nightly" --location "$REGION"; then
    g scheduler jobs update http "$JOB-nightly" --location "$REGION" --schedule "0 3 * * *" \
        --time-zone UTC --uri "$RUN_URI" --http-method POST --oauth-service-account-email "$JOB_SA"
else
    g scheduler jobs create http "$JOB-nightly" --location "$REGION" --schedule "0 3 * * *" \
        --time-zone UTC --uri "$RUN_URI" --http-method POST --oauth-service-account-email "$JOB_SA"
fi
# Off unless asked for: a paused schedule never starts the job, so it uses no compute.
if [ "$SCHEDULE" = on ]; then
    g scheduler jobs resume "$JOB-nightly" --location "$REGION" >/dev/null
else
    g scheduler jobs pause "$JOB-nightly" --location "$REGION" >/dev/null
fi

echo
echo "Done. Nightly retrain is $SCHEDULE. Nothing runs until you start it:"
echo "  gcloud --project $PROJECT run jobs execute $JOB --region $REGION --wait   # retrain once"
echo "  SCHEDULE=on REGION=$REGION gcp/deploy.sh $PROJECT                          # nightly"
