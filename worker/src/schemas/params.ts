import { z } from "@hono/zod-openapi";

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const CHAPTER_RE = /^\d{1,3}$/;

export const SlugSchema = z
	.string()
	.regex(SLUG_RE, "Must be a lowercase slug (a-z, 0-9, hyphens)");

export const ChapterNumberStringSchema = z
	.string()
	.regex(CHAPTER_RE, "Must be a 1-3 digit chapter number");
