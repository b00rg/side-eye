# gcp/: nightly retraining on Google Cloud (T36)

Every night a Cloud Run Job runs `pi/retrain_shape.py`: it reads the labelled alarms from Tiger Data
(the four shape inputs + yes/no label, nothing else), adds the walks in `data/runs_session1/` and
`data/runs_session2/`, retrains the shape model, and writes it to a private Cloud Storage bucket, but
only if the Tiger rows don't make held-out walks worse. The Pi pulls it with `fetch_model.sh`.
(The level model's `pi/retrain.py` is still in the image; see "Level model" below.)

```
Cloud Scheduler 03:00 UTC -> Cloud Run Job (pi/retrain_shape.py) -> gs://<bucket>/shape_model.joblib -> Pi
                               ^ Secret Manager (Tiger URL)
                               ^ Tiger Data events table
```

| File | Runs on | What it does |
|---|---|---|
| `deploy.sh` | Mac | APIs, bucket, secret, service accounts, image, job, paused schedule. Safe to re-run |
| `Dockerfile`, `cloudbuild.yaml` | Cloud Build | `debian:<release>-slim` + Debian's `python3-sklearn`, same as the Pi |
| `lifecycle.json` | bucket | delete versioned models and old object versions after 30 days (matches Tiger's retention) |
| `fetch_model.sh` | Pi | download `shape_model.joblib` (or `MODEL=level`: `model.joblib`), check it's the right kind of model and the scikit-learn version matches, keep `.prev`, restart `canary` |
| `../.gcloudignore` | Mac | what gets uploaded to Cloud Build: never `.env` or keys |

## 1. Once: project and CLI (Mac)
Create a project at console.cloud.google.com, link billing, set a £5 budget alert.
```
brew install --cask google-cloud-sdk
gcloud auth login
```

## 2. Deploy (Mac, repo root; re-run after new code or new walks)
```
DEBIAN_RELEASE=bookworm gcp/deploy.sh <project-id>        # bookworm = Debian 12, trixie = 13: match the Pi
```
Optional: `REGION=` (default `europe-west2`), `BUCKET=` (default `<project>-side-eye-models`),
`RETRAIN_ARGS=--tiger-weight,0.5,--no-demo`, `SCHEDULE=on`. The Tiger URL is read from `.env` into Secret Manager.

**Retraining is off by default.** Every deploy leaves the nightly schedule paused unless you pass
`SCHEDULE=on`, so the job only runs when you start it. A deploy itself only builds the image (a few
Cloud Build minutes); the job doesn't run.

## 3. Retrain by hand (and optionally switch on nightly)
```
gcloud run jobs execute side-eye-retrain --region europe-west2 --wait
gcloud logging read 'resource.type=cloud_run_job AND resource.labels.job_name=side-eye-retrain' --limit 30 --format 'value(textPayload)'
SCHEDULE=on gcp/deploy.sh <project-id>      # optional: nightly at 03:00 UTC
```
The log shows walks-only vs walks + Tiger on the same held-out walks (lingers caught, false alarms, nDCF).

### Testing with dummy data
```
python3 scripts/dummy_events.py add       # 60 labelled events in Tiger, device_id = 'dummy-data'
gcloud run jobs execute side-eye-retrain --region europe-west4 --wait
gcloud storage cat gs://<bucket>/models/shape_latest.json
python3 scripts/dummy_events.py remove    # delete them again (annotate.py's report counts them meanwhile)
```

## 4. The Pi
```
# Mac: key that can only read the bucket
gcloud iam service-accounts keys create side-eye-pi-key.json --iam-account side-eye-pi@<project-id>.iam.gserviceaccount.com
scp side-eye-pi-key.json canary@canary.local: && rm side-eye-pi-key.json
# Pi: gcloud CLI (https://cloud.google.com/sdk/docs/install#deb), then
chmod 600 ~/side-eye-pi-key.json
cd ~/side-eye && gcp/fetch_model.sh <project-id>-side-eye-models
```
Daily pull: `crontab -e` -> `30 3 * * * cd /home/canary/side-eye && gcp/fetch_model.sh <bucket> >> /tmp/fetch_model.log 2>&1`.

## Level model
The image also holds `pi/retrain.py` (the level model, `model.joblib`, for `canary.py live --use-model`):
`gcloud run jobs execute side-eye-retrain --region <region> --command python3 --args pi/retrain.py,--out-dir,/models --wait`,
then on the Pi `MODEL=level gcp/fetch_model.sh <bucket>`.

## Rollback
- Pi: `mv shape_model.joblib.prev shape_model.joblib` (shape_live reloads it by itself).
- Bucket: `gcloud storage ls -a gs://<bucket>/shape_model.joblib`, then copy the older `#generation` over it.
- Stop nightly retraining: re-run `gcp/deploy.sh` without `SCHEDULE=on`, or `gcloud scheduler jobs pause side-eye-retrain-nightly --location <region>`.
- Remove everything: delete the project; `rm ~/side-eye-pi-key.json` on the Pi.

## Troubleshooting
- `builds submit` says PERMISSION_DENIED right after the APIs were enabled: wait 2 minutes and re-run (the API takes time to switch on).
- Build fails with a permission error: the Compute Engine default service account needs `roles/cloudbuild.builds.builder`
  (deploy.sh grants it; new projects leave it with no roles).
- Key creation refused: an organisation policy blocks service-account keys; use a personal project.
- Local test without Google Cloud: `docker build -f gcp/Dockerfile -t side-eye-retrain . && docker run --rm --env-file .env -v "$PWD/out:/models" side-eye-retrain`
