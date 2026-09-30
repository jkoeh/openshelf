CREATE TABLE generation_jobs_next (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES source_books(source_id),
  build_id TEXT NOT NULL,
  start_id TEXT NOT NULL REFERENCES generation_starts(id),
  state TEXT NOT NULL CHECK (state IN ('queued','running','completed','failed','canceled')),
  stage TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  lease_token TEXT,
  lease_until TEXT,
  author_slug TEXT,
  title_slug TEXT,
  error_code TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT INTO generation_jobs_next
  SELECT id,source_id,build_id,start_id,state,stage,attempts,lease_token,lease_until,
         author_slug,title_slug,error_code,created_at,updated_at FROM generation_jobs;
DROP TABLE generation_jobs;
ALTER TABLE generation_jobs_next RENAME TO generation_jobs;
CREATE UNIQUE INDEX one_active_generation
  ON generation_jobs(source_id) WHERE state IN ('queued','running');
CREATE INDEX generation_claim ON generation_jobs(state, created_at);
