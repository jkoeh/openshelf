const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/** Returns true if `value` is a valid URL slug (lowercase alphanumeric + hyphens). */
export function isValidSlug(value: string | undefined): value is string {
	return typeof value === "string" && SLUG_RE.test(value);
}

/** Returns true if `value` is a valid chapter number like "1", "01", "12". */
export function isValidChapter(value: string | undefined): value is string {
	return typeof value === "string" && /^\d{1,3}$/.test(value);
}
