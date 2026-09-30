CREATE TABLE IF NOT EXISTS source_books (
  source_id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  author TEXT NOT NULL,
  epub_url TEXT NOT NULL,
  author_slug TEXT,
  title_slug TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_tokens (
  token TEXT NOT NULL,
  source_id TEXT NOT NULL REFERENCES source_books(source_id) ON DELETE CASCADE,
  PRIMARY KEY (token, source_id)
);
CREATE INDEX IF NOT EXISTS source_tokens_source ON source_tokens(source_id);
CREATE TABLE IF NOT EXISTS generation_starts (
  id TEXT PRIMARY KEY,
  day TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS generation_starts_day ON generation_starts(day);
CREATE TABLE IF NOT EXISTS generation_jobs (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL REFERENCES source_books(source_id),
  build_id TEXT NOT NULL,
  start_id TEXT NOT NULL REFERENCES generation_starts(id),
  state TEXT NOT NULL CHECK (state IN ('queued','running','completed','failed')),
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
CREATE UNIQUE INDEX IF NOT EXISTS one_active_generation
  ON generation_jobs(source_id) WHERE state IN ('queued','running');
CREATE INDEX IF NOT EXISTS generation_claim
  ON generation_jobs(state, created_at);
