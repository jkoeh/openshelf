CREATE TABLE owner_uploads (
  id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL UNIQUE REFERENCES source_books(source_id) ON DELETE CASCADE,
  format TEXT NOT NULL CHECK(format IN ('pdf','docx','txt')),
  sha256 TEXT NOT NULL,
  byte_size INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
