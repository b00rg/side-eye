"""Small, hardware-independent parts of the Canary hackathon prototype."""

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, variance


META_IDS = {0x058E, 0x01AB, 0x0D53}
HEYCYAN_UUID = "7905fff0-b5ce-4e99-a40f-4b1e122d00d0"
DEMO_ID = 0x058E
DEMO_PAYLOAD = bytes.fromhex("CA01")
MODEL_COLUMNS = ("mean_rssi", "var_rssi", "slope", "dwell_s")
MODEL_THRESHOLD = 0.8   # model alert: score >= this on two rows...
MODEL_GAP_S = 2.5       # ...no more than this many seconds apart (same rule nist/compare_models.py scores)


def project_root(script):
    """Where settings.json, data/runs/ and model.joblib live.

    Inside the repo the scripts sit in pi/, so it's the repo root. Deployed flat (the guide's
    /home/canary/canary from Canary_code.zip) it's the scripts' own folder.
    """
    here = Path(script).resolve().parent
    if here.name == "pi" and (here.parent / "nist").is_dir():
        return here.parent
    return here


class ModelAlert:
    """Fires when the linger score is >= MODEL_THRESHOLD twice within MODEL_GAP_S seconds."""

    def __init__(self, threshold=MODEL_THRESHOLD, gap=MODEL_GAP_S):
        self.threshold, self.gap, self.last_high = threshold, gap, None

    def update(self, now, score):
        if score < self.threshold:
            return False
        fire = self.last_high is not None and now - self.last_high <= self.gap
        self.last_high = now
        return fire


def signature(advertisement, demo_only=True):
    """Return a signature name or None. Demo mode accepts only our exact phone packet."""
    manufacturers = advertisement.manufacturer_data
    if demo_only:
        return "phone_simulator" if manufacturers.get(DEMO_ID) == DEMO_PAYLOAD else None
    for company_id in META_IDS:
        if company_id in manufacturers:
            return f"company_0x{company_id:04x}"
    if HEYCYAN_UUID in {u.lower() for u in advertisement.service_uuids}:
        return "heycyan_uuid"
    return None


@dataclass(frozen=True)
class Sample:
    t: float
    raw: int
    smooth: float


@dataclass(frozen=True)
class Snapshot:
    mean_rssi: float
    var_rssi: float
    slope: float
    dwell_s: float
    close_streak_s: float
    packet_rate_hz: float
    latest_rssi: int

    def model_vector(self):
        return [getattr(self, name) for name in MODEL_COLUMNS]


class Tracker:
    def __init__(self):
        self.samples = deque()
        self.last_seen = None

    def add(self, t, rssi):
        if self.last_seen is not None and t - self.last_seen > 10:
            self.samples.clear()
        previous = self.samples[-1].smooth if self.samples else float(rssi)
        smooth = 0.3 * rssi + 0.7 * previous
        self.samples.append(Sample(t, int(rssi), smooth))
        self.last_seen = t
        self.prune(t)

    def prune(self, now):
        while self.samples and self.samples[0].t < now - 10:
            self.samples.popleft()

    def snapshot(self, now, threshold):
        self.prune(now)
        if not self.samples or now - self.samples[-1].t > 2:
            return None
        recent = [s for s in self.samples if s.t >= now - 5]
        if len(recent) < 5:
            return None
        average = mean(s.smooth for s in recent)
        jitter = variance(s.raw for s in recent)
        times = [s.t - recent[0].t for s in recent]
        values = [s.smooth for s in recent]
        t_average = mean(times)
        denominator = sum((t - t_average) ** 2 for t in times)
        slope = (
            sum((t - t_average) * (v - average) for t, v in zip(times, values))
            / denominator
            if denominator
            else 0.0
        )
        dwell = sum(
            min(current.t - previous.t, 2.0)
            for previous, current in zip(self.samples, list(self.samples)[1:])
            if 0 < current.t - previous.t <= 2
            and previous.smooth >= threshold
            and current.smooth >= threshold
        )
        streak = 0.0
        if self.samples[-1].smooth >= threshold:
            samples = list(self.samples)
            for i in range(len(samples) - 1, 0, -1):
                gap = samples[i].t - samples[i - 1].t
                if gap > 2 or samples[i - 1].smooth < threshold:
                    break
                streak += gap
        return Snapshot(
            mean_rssi=average,
            var_rssi=jitter,
            slope=slope,
            dwell_s=dwell,
            close_streak_s=streak,
            packet_rate_hz=len(recent) / 5.0,
            latest_rssi=self.samples[-1].raw,
        )
