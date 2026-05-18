export interface CatalogBook {
  author: string;
  author_slug: string;
  title: string;
  title_slug: string;
  source: string;
  rendition: string;
  total_duration_seconds: number;
  chapter_count: number;
  has_cover?: boolean;
}

export interface CatalogResponse {
  version: number;
  generated_at: string;
  books: CatalogBook[];
  total: number;
  page: number;
  limit: number;
}

export interface ManifestChapter {
  number: number;
  title: string;
  filename: string;
  duration_seconds: number;
  word_count: number;
}

export interface Manifest {
  title: string;
  author: string;
  source: string;
  renditions: Record<string, ManifestRendition>;
}

export interface ManifestRendition {
  voice: string;
  engine: string;
  display: string;
  current_build: string;
  available_builds: string[];
  total_duration_seconds: number;
  chapters: ManifestChapter[];
}

export interface ChapterResponse {
  number: number;
  title: string;
  chunks: string[];
  word_count: number;
  words?: WordEntry[];
}

export interface WordEntry {
  word: string;
  start: number;
  end: number;
  chunk_idx: number;
  element_id?: string;
}
