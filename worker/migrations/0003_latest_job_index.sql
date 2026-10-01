CREATE INDEX IF NOT EXISTS latest_source_job
  ON generation_jobs(source_id, created_at DESC);
