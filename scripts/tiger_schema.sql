-- Side Eye annotated events on Tiger Data (TimescaleDB).
--
-- One row = one case a person can annotate:
--   buzzed  glasses_present   outcome
--   true    true              TP   the Pi buzzed and smart glasses were there
--   true    false             FP   the Pi buzzed but there were no glasses
--   false   true              FN   glasses were there but the Pi stayed quiet (reported by a person)
--   true    NULL              pending: buzzed, not annotated yet
--   false   false             never stored (true negative): blocked by the CHECK constraint
--
-- No raw BLE readings, no MAC addresses, no location.
-- Applied by scripts/tiger_setup.py and safe to re-run.

CREATE TABLE IF NOT EXISTS events (
    event_id        uuid        NOT NULL,   -- generated client-side, so buffered re-uploads are idempotent
    time            timestamptz NOT NULL,   -- when it buzzed, or when the missed glasses were seen
    device_id       text        NOT NULL,   -- which wearable, e.g. the Pi hostname
    buzzed          boolean     NOT NULL,   -- did the Pi raise an alarm
    glasses_present boolean,                -- annotation, NULL until a person labels a buzz
    annotated_at    timestamptz,
    source          text        NOT NULL,   -- pi (automatic buzz) or user (reported miss)
    note            text,                   -- free text from the annotator
    glasses_ref     text,                   -- keyed hash of the BLE address, rotates daily, NULL for user reports
    signature       text,                   -- what matched, e.g. phone_simulator or company_0x058e
    demo            boolean     NOT NULL DEFAULT false, -- true for the nRF Connect phone stand-in
    rule            text,                   -- which rule fired: 3s-streak, level-model or shape-model
    rssi            smallint,
    mean_rssi       real,
    var_rssi        real,
    slope           real,
    dwell_s         real,
    close_streak_s  real,
    ml_score        real,
    -- the shape model's inputs at that second (pi/shape_core.py); NULL for rows from canary.py live
    rise            real,
    drop            real,
    steady          real,
    held            real,
    PRIMARY KEY (event_id, time),
    CONSTRAINT no_true_negatives CHECK (buzzed OR glasses_present IS TRUE)
);

-- Tables created before the shape columns existed get them here (nullable, so this is instant).
ALTER TABLE events ADD COLUMN IF NOT EXISTS rise real;
ALTER TABLE events ADD COLUMN IF NOT EXISTS drop real;
ALTER TABLE events ADD COLUMN IF NOT EXISTS steady real;
ALTER TABLE events ADD COLUMN IF NOT EXISTS held real;

SELECT create_hypertable('events', by_range('time', INTERVAL '1 day'), if_not_exists => TRUE);

CREATE INDEX IF NOT EXISTS events_device_time ON events (device_id, time DESC);
CREATE INDEX IF NOT EXISTS events_pending ON events (time DESC) WHERE buzzed AND glasses_present IS NULL;

-- Each row with its outcome spelled out, for queries and dashboards.
CREATE OR REPLACE VIEW event_outcomes AS
SELECT *,
    CASE
        WHEN buzzed AND glasses_present      THEN 'TP'
        WHEN buzzed AND NOT glasses_present  THEN 'FP'
        WHEN NOT buzzed AND glasses_present  THEN 'FN'
        ELSE 'pending'
    END AS outcome
FROM events;

-- Compress chunks older than a week; delete everything after 30 days (privacy: nothing kept long-term).
ALTER TABLE events SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'device_id',
    timescaledb.compress_orderby = 'time DESC'
);
SELECT add_compression_policy('events', INTERVAL '7 days', if_not_exists => TRUE);
SELECT add_retention_policy('events', INTERVAL '30 days', if_not_exists => TRUE);

-- Hourly TP / FP / FN counts for graphs and slides. Real-time, so new rows show immediately.
CREATE MATERIALIZED VIEW IF NOT EXISTS events_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    time_bucket(INTERVAL '1 hour', time) AS bucket,
    device_id,
    demo,
    count(*) FILTER (WHERE buzzed)                                   AS buzzes,
    count(*) FILTER (WHERE buzzed AND glasses_present)               AS tp,
    count(*) FILTER (WHERE buzzed AND NOT glasses_present)           AS fp,
    count(*) FILTER (WHERE NOT buzzed AND glasses_present)           AS fn,
    count(*) FILTER (WHERE buzzed AND glasses_present IS NULL)       AS pending,
    avg(mean_rssi)                                                   AS avg_mean_rssi
FROM events
GROUP BY bucket, device_id, demo
WITH NO DATA;

SELECT add_continuous_aggregate_policy('events_hourly',
    start_offset => INTERVAL '3 days',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '15 minutes',
    if_not_exists => TRUE);
SELECT add_retention_policy('events_hourly', INTERVAL '30 days', if_not_exists => TRUE);
