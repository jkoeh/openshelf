ALTER TABLE source_books ADD COLUMN language TEXT NOT NULL DEFAULT 'en';
ALTER TABLE source_books ADD COLUMN provenance TEXT NOT NULL DEFAULT '{}';
ALTER TABLE source_books ADD COLUMN epub_sha256 TEXT;
CREATE INDEX source_books_language ON source_books(language, source_id);
