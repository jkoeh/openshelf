ALTER TABLE generation_jobs
  ADD COLUMN priority INTEGER NOT NULL DEFAULT 0 CHECK (priority IN (0, 1));

CREATE INDEX recent_generation_jobs ON generation_jobs(updated_at DESC);
