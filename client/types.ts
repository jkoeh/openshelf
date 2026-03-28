export interface CatalogBook {
  author: string;
  author_slug: string;
  title: string;
  title_slug: string;
  source: string;
  rendition: string;
  total_duration_seconds: number;
  chapter_count: number;
}

export interface CatalogResponse {
  version: number;
  generated_at: string;
  books: CatalogBook[];
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
  rendition: string;
  chunks_version: number;
  generated_at: string;
  total_duration_seconds: number;
  chapters: ManifestChapter[];
}

export interface ChapterResponse {
  number: number;
  title: string;
  chunks: string[];
  word_count: number;
}

export interface WordEntry {
  word: string;
  start: number;
  end: number;
  chunk_idx: number;
  element_id?: string;
}

export interface ChapterAlignment {
  chapter: number;
  words: WordEntry[];
}

export interface AlignmentResponse {
  version: number;
  rendition: string;
  chapters: ChapterAlignment[];
}
