import { z } from "@hono/zod-openapi";

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const CHAPTER_RE = /^\d{1,3}$/;
const BUILD_RE = /^[a-f0-9]{16}$/;

export const SlugSchema = z.string().regex(SLUG_RE, "Must be a lowercase slug (a-z, 0-9, hyphens)");

export const ChapterNumberStringSchema = z
	.string()
	.regex(CHAPTER_RE, "Must be a 1-3 digit chapter number");

export const BuildSchema = z.string().regex(BUILD_RE, "Must be a 16-character hex build ID");
