-- ISSUES
CREATE TABLE issues (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL,
  project_key TEXT,
  created_at TIMESTAMP NOT NULL,
  updated_at TIMESTAMP NOT NULL,
  done_at TIMESTAMP NULL,
  current_status TEXT
);

-- TRANSITIONS (normalized from changelog)
CREATE TABLE transitions (
  id SERIAL PRIMARY KEY,
  issue_id TEXT REFERENCES issues(id),
  from_status TEXT,
  to_status TEXT,
  transitioned_at TIMESTAMP NOT NULL,
  UNIQUE(issue_id, transitioned_at, to_status)
);

-- TIME SLICES (derived)
CREATE TABLE time_slices (
  id SERIAL PRIMARY KEY,
  issue_id TEXT REFERENCES issues(id),
  status TEXT,
  start_at TIMESTAMP NOT NULL,
  end_at TIMESTAMP NOT NULL,
  duration_seconds INTEGER NOT NULL
);

-- ISSUE METRICS
CREATE TABLE metrics_issue (
  issue_id TEXT PRIMARY KEY REFERENCES issues(id),
  cycle_seconds INTEGER,
  active_seconds INTEGER,
  wait_seconds INTEGER
);

-- STATUS METRICS (windowed)
CREATE TABLE metrics_status_window (
  id SERIAL PRIMARY KEY,
  status TEXT,
  window_start TIMESTAMP,
  window_end TIMESTAMP,
  avg_seconds FLOAT,
  p50_seconds FLOAT,
  p90_seconds FLOAT,
  wip_avg FLOAT,
  throughput INTEGER
);

-- ALERTS
CREATE TABLE alerts (
  id SERIAL PRIMARY KEY,
  rule_id TEXT,
  issue_id TEXT,
  status TEXT,
  triggered_at TIMESTAMP,
  payload JSONB
);