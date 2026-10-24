import { z } from "@hono/zod-openapi";

export const JobErrorCode = z.enum(["RIGHTS_NOT_VERIFIED", "BOOK_TOO_LONG", "INVALID_DOCUMENT", "GENERATION_FAILED"]);

export function publicJobError(code: string | null): z.infer<typeof JobErrorCode> | null {
	if (code === "RightsNotVerified") return "RIGHTS_NOT_VERIFIED";
	if (code === "BookTooLong") return "BOOK_TOO_LONG";
	if (code === "InvalidOwnerDocument") return "INVALID_DOCUMENT";
	return code ? "GENERATION_FAILED" : null;
}
