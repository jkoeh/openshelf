import { z } from "@hono/zod-openapi";

export const ErrorSchema = z
	.object({
		error: z.object({
			code: z.string().openapi({ example: "NOT_FOUND" }),
			message: z.string().openapi({ example: "Book not found: franz-kafka/the-trial" }),
		}),
	})
	.openapi("Error");

export type ErrorBody = z.infer<typeof ErrorSchema>;
