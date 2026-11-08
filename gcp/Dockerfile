# Nightly retrain job for Cloud Run (built by gcp/deploy.sh via gcp/cloudbuild.yaml).
# Debian's own python3-sklearn, same release as the Pi, so the pickled model loads there.
ARG DEBIAN_RELEASE=bookworm
FROM debian:${DEBIAN_RELEASE}-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates python3 python3-numpy python3-sklearn python3-joblib python3-psycopg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pi/ pi/
COPY data/runs/ data/runs/

# TIMESCALE_SERVICE_URL comes from Secret Manager; /models is the Cloud Storage bucket mount.
ENTRYPOINT ["python3", "pi/retrain.py", "--out-dir", "/models"]
CMD []
