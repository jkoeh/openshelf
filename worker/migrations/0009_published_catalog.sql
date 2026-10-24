CREATE TABLE published_books (
  author_slug TEXT NOT NULL,
  title_slug TEXT NOT NULL,
  author TEXT NOT NULL,
  title TEXT NOT NULL,
  source TEXT NOT NULL,
  rendition TEXT NOT NULL,
  current_build TEXT NOT NULL,
  total_duration_seconds REAL NOT NULL,
  section_count INTEGER NOT NULL,
  has_cover INTEGER NOT NULL,
  title_search TEXT NOT NULL,
  author_search TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (author_slug, title_slug)
);
CREATE INDEX published_books_title ON published_books(title_search, author_search);
