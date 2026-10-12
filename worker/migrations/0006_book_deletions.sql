CREATE INDEX IF NOT EXISTS source_books_publication ON source_books(author_slug, title_slug);
CREATE TABLE IF NOT EXISTS book_deletions (
  author_slug TEXT NOT NULL,
  title_slug TEXT NOT NULL,
  lease_token TEXT NOT NULL,
  lease_until TEXT NOT NULL,
  PRIMARY KEY (author_slug, title_slug)
);
CREATE TRIGGER IF NOT EXISTS prevent_generation_during_deletion
BEFORE INSERT ON generation_jobs
WHEN EXISTS (
  SELECT 1 FROM source_books s JOIN book_deletions d
    ON s.author_slug=d.author_slug AND s.title_slug=d.title_slug
  WHERE s.source_id=NEW.source_id
)
BEGIN SELECT RAISE(ABORT, 'BOOK_DELETING'); END;
CREATE TRIGGER IF NOT EXISTS prevent_retry_during_deletion
BEFORE UPDATE OF state ON generation_jobs
WHEN NEW.state IN ('queued', 'running') AND EXISTS (
  SELECT 1 FROM source_books s JOIN book_deletions d
    ON s.author_slug=d.author_slug AND s.title_slug=d.title_slug
  WHERE s.source_id=NEW.source_id
)
BEGIN SELECT RAISE(ABORT, 'BOOK_DELETING'); END;
