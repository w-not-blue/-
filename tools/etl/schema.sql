-- Catalog schema. Every fact and dimension row carries source_id so that any
-- number used in a report can be traced back to a publisher and a URL.

CREATE TABLE IF NOT EXISTS meta_data_source (
    source_id     TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    publisher     TEXT,
    url           TEXT,
    license       TEXT,
    retrieved_at  TEXT,
    sha256        TEXT,
    etl_script    TEXT,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS meta_dataset_version (
    version_id  TEXT PRIMARY KEY,
    source_id   TEXT,
    table_name  TEXT,
    row_count   INTEGER,
    min_ts      TEXT,
    max_ts      TEXT,
    hash        TEXT,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS dim_ambient_hourly (
    site_id    TEXT NOT NULL,
    ts         TEXT NOT NULL,
    temp_c     REAL,
    humidity   REAL,
    wind_ms    REAL,
    source_id  TEXT,
    version_id TEXT
);

CREATE TABLE IF NOT EXISTS dim_server_type (
    server_type TEXT PRIMARY KEY,
    max_load    REAL,
    max_temp    REAL,
    base_power  REAL,
    max_power   REAL,
    efficiency  REAL,
    source_id   TEXT
);

CREATE TABLE IF NOT EXISTS dim_pue_benchmark (
    standard_id TEXT,
    grade       TEXT,
    pue_limit   REAL,
    applies_to  TEXT,
    source_id   TEXT
);

CREATE TABLE IF NOT EXISTS dim_emission_factor (
    region           TEXT NOT NULL,
    year             INTEGER NOT NULL,
    kg_co2_per_kwh   REAL,
    source_id        TEXT
);

CREATE TABLE IF NOT EXISTS fact_task_arrivals (
    trace_id           TEXT NOT NULL,
    seq                INTEGER NOT NULL,
    hour               INTEGER NOT NULL,
    load_units         INTEGER NOT NULL,
    priority           INTEGER,
    workload_class     TEXT,
    duration_h         INTEGER,
    deadline_offset_h  INTEGER,
    source_id          TEXT,
    PRIMARY KEY (trace_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_ambient_site_ts ON dim_ambient_hourly (site_id, ts);
CREATE INDEX IF NOT EXISTS idx_tasks_trace_hour ON fact_task_arrivals (trace_id, hour);

CREATE TABLE IF NOT EXISTS dim_school (
    school_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    school_name TEXT NOT NULL,
    province    TEXT,
    city        TEXT,
    level       TEXT,
    department  TEXT,
    source_id   TEXT
);

CREATE TABLE IF NOT EXISTS dim_city_daily (
    city       TEXT NOT NULL,
    date       TEXT NOT NULL,
    tmax_c     REAL,
    tmin_c     REAL,
    tmean_c    REAL,
    source_id  TEXT,
    PRIMARY KEY (city, date)
);

CREATE INDEX IF NOT EXISTS idx_school_name ON dim_school (school_name);
CREATE INDEX IF NOT EXISTS idx_school_city ON dim_school (city);
CREATE INDEX IF NOT EXISTS idx_city_daily ON dim_city_daily (city, date);
