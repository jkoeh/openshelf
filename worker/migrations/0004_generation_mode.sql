ALTER TABLE generation_jobs
  ADD COLUMN mode TEXT NOT NULL DEFAULT 'standard'
  CHECK (mode IN ('standard', 'expressive'));
