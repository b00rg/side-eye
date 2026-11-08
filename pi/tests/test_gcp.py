"""Tests for the Google Cloud retraining setup in gcp/.

Offline (default): deploy.sh and fetch_model.sh run against a fake `gcloud` that only records its
arguments, so nothing touches Google Cloud and nothing costs money.

Opt-in:
    SIDE_EYE_DOCKER_TESTS=1 python3 -m unittest tests.test_gcp -v      # build the image locally
    SIDE_EYE_GCP_TESTS=1 SIDE_EYE_GCP_PROJECT=side-eye-514022 SIDE_EYE_GCP_REGION=europe-west4 \
        python3 -m unittest tests.test_gcp -v                           # read-only checks of the real project
"""

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GCP = ROOT / "gcp"
FAKE_URL = "postgres://user:not-a-real-password@db.example.com:5432/tsdb?sslmode=require"

# Records every call (one line of arguments each) and answers just enough for the scripts.
FAKE_GCLOUD = r"""#!/bin/sh
printf '%s\n' "$*" >> "$GCLOUD_LOG"
if [ "$1 $2" = "storage cp" ]; then
    shift 2
    for a; do dest=$a; done
    for a; do case $a in gs://*) cp "$FAKE_BUCKET/${a#gs://*/}" "$dest" || exit 1;; esac; done
    exit 0
fi
case "$*" in
    *"auth list"*) exit 0 ;;
    *"projects describe"*) echo 123456789; exit 0 ;;
    *"secrets versions access"*) echo "an-older-url"; exit 0 ;;
    *"--data-file=-"*) cat > "$GCLOUD_STDIN"; exit 0 ;;
    *" describe "*) [ -n "${GCLOUD_EXISTS:-}" ] && exit 0 || exit 1 ;;
esac
exit 0
"""
FAKE_SYSTEMCTL = "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$SYSTEMCTL_LOG\"\nexit 3\n"  # 3 = not active


def write_exe(path, text):
    path.write_text(text)
    path.chmod(0o755)


def gcloudignored(path, patterns):
    """Enough of .gcloudignore's gitignore rules for the patterns we use."""
    parts = path.split("/")
    for pattern in patterns:
        if pattern.endswith("/"):
            name = pattern.rstrip("/")
            if name in parts[:-1] or path.startswith(name + "/"):
                return True
        elif "/" in pattern:
            if fnmatch.fnmatch(path, pattern):
                return True
        elif any(fnmatch.fnmatch(part, pattern) for part in parts):
            return True
    return False


class FakeCloud(unittest.TestCase):
    """A temp dir with fake gcloud/systemctl on PATH and a log of every call."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        write_exe(bin_dir / "gcloud", FAKE_GCLOUD)
        write_exe(bin_dir / "systemctl", FAKE_SYSTEMCTL)
        # The scripts' python3 = this one (has sklearn). A wrapper, not a symlink: a symlinked
        # virtualenv python loses the virtualenv's packages.
        write_exe(bin_dir / "python3", f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
        self.log = self.tmp / "gcloud.log"
        self.log.touch()
        self.env = {
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(self.tmp / "home"),
            "GCLOUD_LOG": str(self.log),
            "GCLOUD_STDIN": str(self.tmp / "stdin"),
            "SYSTEMCTL_LOG": str(self.tmp / "systemctl.log"),
        }
        (self.tmp / "home").mkdir()

    def calls(self):
        return self.log.read_text().splitlines()

    def called(self, words):
        return [line for line in self.calls() if words in line]


class DeployTest(FakeCloud):
    def setUp(self):
        super().setUp()
        env_file = self.tmp / "test.env"
        env_file.write_text(f"# comment\nTIMESCALE_SERVICE_URL={FAKE_URL}\nPGUSER=x\n")
        self.env["SIDE_EYE_ENV"] = str(env_file)

    def deploy(self, **extra):
        env = {**self.env, "REGION": "europe-west4", **extra}
        return subprocess.run(["sh", str(GCP / "deploy.sh"), "test-project"], env=env,
                              capture_output=True, text=True, timeout=60)

    def test_schedule_is_off_by_default_and_job_never_runs(self):
        result = self.deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.called("scheduler jobs pause side-eye-retrain-nightly"))
        self.assertFalse(self.called("scheduler jobs resume"))
        self.assertFalse(self.called("run jobs execute"))
        self.assertIn("Nightly retrain is off", result.stdout)

    def test_schedule_on_resumes(self):
        result = self.deploy(SCHEDULE="on")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.called("scheduler jobs resume side-eye-retrain-nightly"))
        self.assertFalse(self.called("scheduler jobs pause"))
        self.assertFalse(self.called("run jobs execute"))

    def test_redeploy_pauses_a_running_schedule(self):
        result = self.deploy(GCLOUD_EXISTS="1")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.called("scheduler jobs update http side-eye-retrain-nightly"))
        self.assertTrue(self.called("scheduler jobs pause side-eye-retrain-nightly"))

    def test_bad_schedule_value_stops_before_any_cloud_call(self):
        result = self.deploy(SCHEDULE="maybe")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_missing_credentials_stops_before_any_cloud_call(self):
        result = self.deploy(SIDE_EYE_ENV=str(self.tmp / "missing.env"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.calls(), [])

    def test_tiger_url_goes_through_stdin_never_arguments(self):
        self.assertEqual(self.deploy().returncode, 0)
        self.assertNotIn("not-a-real-password", self.log.read_text())
        self.assertEqual((self.tmp / "stdin").read_text(), FAKE_URL)
        self.assertTrue(self.called("secrets create side-eye-tiger-url"))

    def test_changed_tiger_url_adds_a_secret_version(self):
        self.assertEqual(self.deploy(GCLOUD_EXISTS="1").returncode, 0)
        self.assertTrue(self.called("secrets versions add side-eye-tiger-url"))
        self.assertFalse(self.called("secrets create"))

    def test_first_deploy_creates_job_with_secret_and_bucket_mount(self):
        self.assertEqual(self.deploy(RETRAIN_ARGS="--tiger-weight,0.5").returncode, 0)
        [create] = self.called("run jobs create side-eye-retrain")
        self.assertIn("--set-secrets TIMESCALE_SERVICE_URL=side-eye-tiger-url:latest", create)
        self.assertIn("--add-volume name=models,type=cloud-storage,bucket=test-project-side-eye-models", create)
        self.assertIn("--add-volume-mount volume=models,mount-path=/models", create)
        self.assertIn("--service-account side-eye-retrain@test-project.iam.gserviceaccount.com", create)
        self.assertIn("--args --tiger-weight,0.5", create)
        self.assertIn("--region europe-west4", create)

    def test_redeploy_updates_job_without_re_adding_volumes(self):
        self.assertEqual(self.deploy(GCLOUD_EXISTS="1").returncode, 0)
        [update] = self.called("run jobs update side-eye-retrain")
        self.assertNotIn("--add-volume", update)
        self.assertFalse(self.called("run jobs create"))

    def test_bucket_is_private_and_pi_can_only_read(self):
        self.assertEqual(self.deploy().returncode, 0)
        [create] = self.called("storage buckets create gs://test-project-side-eye-models")
        self.assertIn("--public-access-prevention", create)
        self.assertIn("--uniform-bucket-level-access", create)
        pi_grants = [line for line in self.calls() if "serviceAccount:side-eye-pi@" in line]
        self.assertEqual(len(pi_grants), 1)
        self.assertIn("--role roles/storage.objectViewer", pi_grants[0])

    def test_build_uses_debian_release_and_grants_build_role(self):
        self.assertEqual(self.deploy(DEBIAN_RELEASE="trixie").returncode, 0)
        [build] = self.called("builds submit")
        self.assertIn("_DEBIAN_RELEASE=trixie", build)
        self.assertIn("--config gcp/cloudbuild.yaml", build)
        [grant] = self.called("123456789-compute@developer.gserviceaccount.com")
        self.assertIn("roles/cloudbuild.builds.builder", grant)


class FetchModelTest(FakeCloud):
    """fetch_model.sh, run from a copy of the repo layout so the real model.joblib is never touched."""

    def setUp(self):
        super().setUp()
        import joblib
        import sklearn
        from sklearn.ensemble import RandomForestClassifier

        self.repo = self.tmp / "repo"
        (self.repo / "gcp").mkdir(parents=True)
        shutil.copy(GCP / "fetch_model.sh", self.repo / "gcp" / "fetch_model.sh")
        self.bucket = self.tmp / "bucket"
        (self.bucket / "models").mkdir(parents=True)
        model = RandomForestClassifier(n_estimators=2, random_state=0).fit(
            [[-55, 1, 0, 5], [-85, 1, 0, 0]], [1, 0])
        joblib.dump(model, self.bucket / "model.joblib")
        self.write_info(sklearn.__version__)
        self.env["FAKE_BUCKET"] = str(self.bucket)
        (self.tmp / "home" / "side-eye-pi-key.json").write_text("{}")

    def write_info(self, sklearn_version):
        (self.bucket / "models" / "latest.json").write_text(json.dumps({
            "sklearn": sklearn_version, "model": "model-20261107T030000Z.joblib",
            "tiger_events": 3, "walks": 24}))

    def fetch(self, *args):
        return subprocess.run(["sh", str(self.repo / "gcp" / "fetch_model.sh"), "test-bucket", *args],
                              env=self.env, capture_output=True, text=True, timeout=60)

    def test_installs_model_and_keeps_previous(self):
        (self.repo / "model.joblib").write_text("old model")
        result = self.fetch()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.repo / "model.joblib.prev").read_text(), "old model")
        self.assertEqual((self.repo / "model.joblib").read_bytes(), (self.bucket / "model.joblib").read_bytes())
        self.assertEqual(json.loads((self.repo / "model.json").read_text())["walks"], 24)
        self.assertIn("Installed model-20261107T030000Z.joblib", result.stdout)
        self.assertTrue(self.called("storage cp --quiet gs://test-bucket/models/latest.json gs://test-bucket/model.joblib"))

    def test_uses_its_own_gcloud_login(self):
        self.assertEqual(self.fetch().returncode, 0)
        [activate] = self.called("auth activate-service-account")
        self.assertIn("side-eye-pi-key.json", activate)

    def test_refuses_model_from_other_sklearn(self):
        (self.repo / "model.joblib").write_text("old model")
        self.write_info("0.0.1")
        result = self.fetch()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not installing", result.stderr)
        self.assertEqual((self.repo / "model.joblib").read_text(), "old model")

    def test_force_installs_anyway(self):
        self.write_info("0.0.1")
        result = self.fetch("--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.repo / "model.joblib").is_file())

    def test_rejects_model_with_wrong_features(self):
        import joblib
        from sklearn.ensemble import RandomForestClassifier

        joblib.dump(RandomForestClassifier(n_estimators=2).fit([[1, 2], [3, 4]], [0, 1]),
                    self.bucket / "model.joblib")
        (self.repo / "model.joblib").write_text("old model")
        self.assertNotEqual(self.fetch().returncode, 0)
        self.assertEqual((self.repo / "model.joblib").read_text(), "old model")

    def test_missing_model_in_bucket_fails_cleanly(self):
        (self.bucket / "model.joblib").unlink()
        self.assertNotEqual(self.fetch().returncode, 0)
        self.assertFalse((self.repo / "model.joblib").exists())


class ConfigTest(unittest.TestCase):
    def test_image_writes_to_the_bucket_mount(self):
        dockerfile = (GCP / "Dockerfile").read_text()
        mount = re.search(r"mount-path=(\S+?)\"", (GCP / "deploy.sh").read_text()).group(1)
        self.assertIn(f'"--out-dir", "{mount}"', dockerfile)

    def test_image_uses_debian_sklearn_not_pip(self):
        dockerfile = (GCP / "Dockerfile").read_text()
        self.assertIn("python3-sklearn", dockerfile)
        self.assertNotIn("pip", dockerfile)
        self.assertIn("FROM debian:${DEBIAN_RELEASE}-slim", dockerfile)

    def test_image_contains_code_and_walks(self):
        dockerfile = (GCP / "Dockerfile").read_text()
        self.assertIn("COPY pi/ pi/", dockerfile)
        self.assertIn("COPY data/runs/ data/runs/", dockerfile)

    def test_cloudbuild_uses_the_gcp_dockerfile(self):
        text = (GCP / "cloudbuild.yaml").read_text()
        self.assertIn('"-f", "gcp/Dockerfile"', text)
        self.assertIn("DEBIAN_RELEASE=${_DEBIAN_RELEASE}", text)

    def test_lifecycle_never_deletes_the_live_model(self):
        rules = json.loads((GCP / "lifecycle.json").read_text())["rule"]
        self.assertTrue(rules)
        for rule in rules:
            condition = rule["condition"]
            self.assertTrue(condition.get("matchesPrefix") == ["models/model-"]
                            or "daysSinceNoncurrentTime" in condition, rule)

    def test_gcloudignore_keeps_secrets_out_of_cloud_build(self):
        patterns = [line.strip() for line in (ROOT / ".gcloudignore").read_text().splitlines()
                    if line.strip() and not line.startswith("#")]
        for secret in (".env", ".env.local", "tiger-cloud-trinity-gals-credentials.env",
                       "side-eye-pi-key.json", ".git/config", "data/alarm_buffer.jsonl", "model.joblib"):
            self.assertTrue(gcloudignored(secret, patterns), f"{secret} would be uploaded")
        for needed in ("pi/retrain.py", "pi/canary_core.py", "data/runs/20261107-linger.csv",
                       "gcp/Dockerfile", "gcp/cloudbuild.yaml"):
            self.assertFalse(gcloudignored(needed, patterns), f"{needed} would be left out")

    def test_scripts_parse(self):
        for script in ("deploy.sh", "fetch_model.sh"):
            result = subprocess.run(["sh", "-n", str(GCP / script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


@unittest.skipUnless(os.environ.get("SIDE_EYE_DOCKER_TESTS"), "set SIDE_EYE_DOCKER_TESTS=1 (needs Docker)")
class DockerImageTest(unittest.TestCase):
    def test_image_builds_and_retrain_loads(self):
        tag = "side-eye-retrain:test"
        build = subprocess.run(["docker", "build", "-q", "-f", "gcp/Dockerfile", "-t", tag, "."],
                               cwd=ROOT, capture_output=True, text=True, timeout=900)
        self.assertEqual(build.returncode, 0, build.stderr)
        run = subprocess.run(["docker", "run", "--rm", tag, "--help"], capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn("--out-dir", run.stdout)
        versions = subprocess.run(
            ["docker", "run", "--rm", "--entrypoint", "python3", tag, "-c",
             "import sklearn, psycopg, joblib; print(sklearn.__version__)"],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(versions.returncode, 0, versions.stderr)


@unittest.skipUnless(os.environ.get("SIDE_EYE_GCP_TESTS"), "set SIDE_EYE_GCP_TESTS=1 (read-only, real project)")
class LiveProjectTest(unittest.TestCase):
    """Read-only: describes what deploy.sh created. Never runs the job."""

    project = os.environ.get("SIDE_EYE_GCP_PROJECT", "side-eye-514022")
    region = os.environ.get("SIDE_EYE_GCP_REGION", "europe-west4")

    def gcloud(self, *args):
        result = subprocess.run(["gcloud", "--project", self.project, *args, "--format", "json"],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_schedule_is_paused(self):
        job = self.gcloud("scheduler", "jobs", "describe", "side-eye-retrain-nightly", "--location", self.region)
        self.assertEqual(job["state"], "PAUSED", "nightly retraining is on (deploy with SCHEDULE=off to pause)")

    def test_job_mounts_bucket_and_reads_secret(self):
        job = self.gcloud("run", "jobs", "describe", "side-eye-retrain", "--region", self.region)
        text = json.dumps(job)
        self.assertIn("side-eye-tiger-url", text)
        self.assertIn("/models", text)
        self.assertIn(f"{self.project}-side-eye-models", text)

    def test_bucket_is_private(self):
        bucket = self.gcloud("storage", "buckets", "describe", f"gs://{self.project}-side-eye-models")
        self.assertEqual(bucket.get("public_access_prevention"), "enforced")
        self.assertTrue(bucket.get("uniform_bucket_level_access"))


if __name__ == "__main__":
    unittest.main()
