"""Upload alarms and their annotations to Tiger Data (table `events`).

Rows come from three places:
- the Pi buzzes                 -> buzzed=true, glasses_present=NULL (pending)
- the wire button on the Pi     -> labels the last buzz TP/FP, or reports a miss (FN)
- scripts/annotate.py           -> the same labels from a laptop

Privacy rules this module enforces:
- Nothing is uploaded unless the Pi buzzed or a person says glasses were there.
- The BLE address never leaves the Pi. It is replaced by a keyed hash whose key is
  random, held only in memory, and replaced every day (and on every restart).
- No location, no raw RSSI stream.

Uploads run on a background thread so a slow network can never delay the buzzer.
If Tiger Data is unreachable, operations are appended in order to
data/alarm_buffer.jsonl and replayed later; event_id makes re-sent inserts idempotent.
"""

import hashlib
import hmac
import json
import os
import queue
import secrets
import socket
import threading
import uuid
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BUFFER = ROOT / "data" / "alarm_buffer.jsonl"
SHAPE_COLUMNS = ("rise", "drop", "steady", "held")   # shape_core.SHAPE; filled by shape_live.py, else NULL
ENV_CANDIDATES = (ROOT / ".env", ROOT.parent / "tiger-cloud-trinity-gals-credentials.env")
COLUMNS = (
    "event_id", "time", "device_id", "buzzed", "glasses_present", "annotated_at", "source", "note",
    "glasses_ref", "signature", "demo", "rule",
    "rssi", "mean_rssi", "var_rssi", "slope", "dwell_s", "close_streak_s", "ml_score",
    *SHAPE_COLUMNS,
)
INSERT_SQL = (
    f"INSERT INTO events ({', '.join(COLUMNS)}) "
    f"VALUES ({', '.join('%s' for _ in COLUMNS)}) "
    "ON CONFLICT DO NOTHING"
)
LABEL_SQL = (
    "UPDATE events SET glasses_present = %s, annotated_at = %s "
    "WHERE event_id = %s AND time = %s"
)
RETRY_SECONDS = 30


def read_env_file(path):
    """Minimal KEY=VALUE parser so the Pi doesn't need python-dotenv."""
    values = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip("'\"")
    return values


def service_url():
    """TIMESCALE_SERVICE_URL from the environment, $SIDE_EYE_ENV, or a known env file."""
    if os.environ.get("TIMESCALE_SERVICE_URL"):
        return os.environ["TIMESCALE_SERVICE_URL"]
    candidates = [Path(os.environ["SIDE_EYE_ENV"])] if os.environ.get("SIDE_EYE_ENV") else []
    for path in candidates + list(ENV_CANDIDATES):
        if path.is_file():
            url = read_env_file(path).get("TIMESCALE_SERVICE_URL")
            if url:
                return url
    return None


class GlassesRef:
    """Stable-for-a-day pseudonym for a BLE address. The key never leaves memory."""

    def __init__(self, today=date.today):
        self._today = today
        self._day = None
        self._key = None

    def __call__(self, address):
        day = self._today()
        if day != self._day:
            self._day, self._key = day, secrets.token_bytes(32)
        return hmac.new(self._key, address.encode(), hashlib.sha256).hexdigest()[:16]


def device_id():
    return os.environ.get("SIDE_EYE_DEVICE_ID") or socket.gethostname()


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _shape(shape):
    """The shape model's four inputs at that second (shape_core.ShapeTracker), or NULLs."""
    if shape is None:
        return dict.fromkeys(SHAPE_COLUMNS)
    return {name: round(float(value), 4) for name, value in zip(SHAPE_COLUMNS, shape)}


def _features(snap, ml_score, shape=None):
    if snap is None:
        return {**dict.fromkeys(("rssi", "mean_rssi", "var_rssi", "slope", "dwell_s", "close_streak_s", "ml_score")),
                **_shape(None)}
    return {**_shape(shape),
        "rssi": int(snap.latest_rssi),
        "mean_rssi": round(snap.mean_rssi, 3),
        "var_rssi": round(snap.var_rssi, 3),
        "slope": round(snap.slope, 4),
        "dwell_s": round(snap.dwell_s, 3),
        "close_streak_s": round(snap.close_streak_s, 3),
        "ml_score": None if ml_score is None else round(float(ml_score), 4),
    }


def make_alarm(address, signature, snap, ml_score, glasses_ref, rule="3s-streak", now=None, shape=None):
    """Row for one buzz, pending annotation. `address` is hashed here and not stored."""
    return {
        "event_id": str(uuid.uuid4()),
        "time": (now or datetime.now(timezone.utc)).isoformat(),
        "device_id": device_id(),
        "buzzed": True,
        "glasses_present": None,
        "annotated_at": None,
        "source": "pi",
        "note": None,
        "glasses_ref": glasses_ref(address),
        "signature": signature or "unknown",
        "demo": signature == "phone_simulator",
        "rule": rule,
        **_features(snap, ml_score, shape),
    }


def make_missed(glasses_ref, address=None, signature=None, snap=None, ml_score=None, source="button", note=None,
                shape=None):
    """Row for glasses that were there with no buzz (FN).

    If the Pi was tracking a matching device when the person reported the miss, its features are
    attached, so the row can be used for training; otherwise only time and device are stored.
    """
    return {
        "event_id": str(uuid.uuid4()),
        "time": _now_iso(),
        "device_id": device_id(),
        "buzzed": False,
        "glasses_present": True,
        "annotated_at": _now_iso(),
        "source": source,
        "note": note,
        "glasses_ref": glasses_ref(address) if address else None,
        "signature": signature,
        "demo": signature == "phone_simulator",
        "rule": None,
        **_features(snap, ml_score, shape),
    }


def _row(alarm):
    values = dict(alarm)
    for key in ("time", "annotated_at"):
        if values.get(key):
            values[key] = datetime.fromisoformat(values[key])
    return tuple(values.get(name) for name in COLUMNS)


def _default_connect(url):
    import psycopg

    return psycopg.connect(url, connect_timeout=5)


class AlarmStore:
    def __init__(self, url, buffer_path=BUFFER, connect=_default_connect):
        self.url = url
        self.buffer_path = Path(buffer_path)
        self._connect = connect
        self._conn = None
        self._queue = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="alarm-store", daemon=True)
        self._thread.start()

    @classmethod
    def from_env(cls):
        """Return a store, or None (with the reason printed) if uploads can't be enabled."""
        url = service_url()
        if not url:
            print("Alarm upload off: no TIMESCALE_SERVICE_URL (.env or $SIDE_EYE_ENV).")
            return None
        try:
            import psycopg  # noqa: F401
        except ImportError:
            print("Alarm upload off: `pip install \"psycopg[binary]\"` on this device.")
            return None
        print("Alarm upload on: buzzes and annotations go to Tiger Data (no MAC addresses).")
        return cls(url)

    def record(self, row):
        """Queue a new event row (a buzz or a reported miss). Never blocks."""
        self._queue.put_nowait({"op": "insert", "row": row})

    def label(self, alarm, glasses_present):
        """Queue an annotation for a buzz this store recorded earlier. Never blocks."""
        self._queue.put_nowait({
            "op": "label", "event_id": alarm["event_id"], "time": alarm["time"],
            "glasses_present": bool(glasses_present), "annotated_at": _now_iso(),
        })

    def close(self, timeout=5.0):
        self._queue.put(None)
        self._thread.join(timeout)

    # --- background thread ---
    def _run(self):
        while True:
            try:
                op = self._queue.get(timeout=RETRY_SECONDS)
            except queue.Empty:
                self._try_flush_buffer()
                continue
            if op is None:
                self._try_flush_buffer()
                self._disconnect()
                return
            try:
                self._flush_buffer()
                self._apply([op])
            except Exception as error:  # network down, DNS, auth, ...
                print(f"Upload failed ({type(error).__name__}); buffered locally.", flush=True)
                self._disconnect()
                self._append_buffer(op)

    def _apply(self, ops):
        if self._conn is None:
            self._conn = self._connect(self.url)
        with self._conn.cursor() as cursor:
            for op in ops:
                if op.get("op", "insert") == "insert":
                    cursor.execute(INSERT_SQL, _row(op.get("row", op)))
                else:
                    cursor.execute(LABEL_SQL, (
                        op["glasses_present"], datetime.fromisoformat(op["annotated_at"]),
                        op["event_id"], datetime.fromisoformat(op["time"]),
                    ))
        self._conn.commit()

    def _append_buffer(self, op):
        self.buffer_path.parent.mkdir(parents=True, exist_ok=True)
        with self.buffer_path.open("a") as out:
            out.write(json.dumps(op) + "\n")

    def _flush_buffer(self):
        if not self.buffer_path.exists():
            return
        ops = [json.loads(line) for line in self.buffer_path.read_text().splitlines() if line.strip()]
        if ops:
            self._apply(ops)
        self.buffer_path.unlink()

    def _try_flush_buffer(self):
        try:
            self._flush_buffer()
        except Exception:
            self._disconnect()

    def _disconnect(self):
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
