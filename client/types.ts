export interface CatalogBook {
  author: string;
  author_slug: string;
  title: string;
  title_slug: string;
  source: string;
  rendition: string;
  current_build: string;
  total_duration_seconds: number;
  section_count: number;
  has_cover?: boolean;
}

export interface SourceBook {
  source_id: string;
  title: string;
  author: string;
  state: "ready_to_generate" | "queued" | "running" | "failed" | "available";
  job_id: string | null;
  author_slug: string | null;
  title_slug: string | null;
}

export interface GenerationJob {
  id: string;
  source_id: string;
  build_id: string;
  state: "queued" | "running" | "completed" | "failed";
  stage: string;
  attempts: number;
  author_slug: string | null;
  title_slug: string | null;
  error_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface CatalogResponse {
  version: number;
  generated_at: string;
  books: CatalogBook[];
  total: number;
  page: number;
  limit: number;
}

export type SectionType =
  | "opening_credits"
  | "chapter"
  | "prologue"
  | "epilogue"
  | "epigraph"
  | "preface"
  | "introduction"
  | "afterword"
  | "appendix"
  | "part"
  | "closing_credits"
  | "other";

export interface SectionHeading {
  display_label: string;
  display_title: string;
  spoken_text: string;
}

export interface ManifestSection {
  sequence: number;
  section_type: SectionType;
  ordinal: number | null;
  display_label: string;
  display_title: string;
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
  sections: ManifestSection[];
}

export interface BookBuildsResponse {
  title: string;
  author: string;
  source: string;
  renditions: Record<string, BookBuildsRendition>;
}

export interface BookBuildsRendition {
  voice: string;
  engine: string;
  display: string;
  current_build: string;
  builds: BookBuildOption[];
}

export interface BookBuildOption {
  build: string;
  rendition: string;
  voice: string;
  engine: string;
  pipeline_version: string;
  is_current: boolean;
  uploaded_at: string;
  total_duration_seconds: number;
  section_count: number;
  sections: ManifestSection[];
}

export interface SectionResponse {
  sequence: number;
  section_type: SectionType;
  ordinal: number | null;
  heading: SectionHeading;
  chunks: string[];
  word_count: number;
  words?: WordEntry[];
}

export interface WordEntry {
  word: string;
  start: number;
  end: number;
  region: "heading" | "body";
  chunk_idx: number | null;
  element_id?: string;
}
