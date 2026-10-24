import { createRoute, z } from "@hono/zod-openapi";
import { ErrorSchema } from "../schemas/error";
import type { Env } from "../types";
import { adminCredentialStatus } from "../utils/google-admin";
import { credentialStatus, noStore } from "../utils/job-auth";
import { createOpenAPIApp } from "../utils/openapi-app";

const MAX_BYTES = 16 * 1024 * 1024;
const Format = z.enum(["pdf", "docx", "txt"]);
const Upload = z.object({
	id: z.string().uuid(),
	source_id: z.string(),
	title: z.string(),
	author: z.string(),
	language: z.enum(["en", "zh"]),
	format: Format,
	created_at: z.string(),
	author_slug: z.string().nullable().optional(),
	title_slug: z.string().nullable().optional(),
	job_id: z.string().nullable().optional(),
});
const Meta = z.object({
	title: z
		.string()
		.trim()
		.min(1)
		.max(300)
		.regex(/^[^\p{Cc}]+$/u),
	author: z
		.string()
		.trim()
		.min(1)
		.max(200)
		.regex(/^[^\p{Cc}]+$/u),
	language: z.enum(["en", "zh"]),
	format: Format,
	publish: z.literal("yes"),
});
const Id = z.object({ id: z.string().uuid() });
const error = { description: "Error", content: { "application/json": { schema: ErrorSchema } } };
const owner = createOpenAPIApp<{ Bindings: Env }>();
const internal = createOpenAPIApp<{ Bindings: Env }>();
const create = createRoute({
	method: "post",
	path: "/",
	tags: ["owner-documents"],
	summary: "Owner uploads a document with rights and public-publication acknowledgement",
	request: {
		query: Meta,
		body: {
			content: { "application/octet-stream": { schema: { type: "string", format: "binary" } } },
		},
	},
	responses: {
		201: {
			description: "Uploaded; request generation separately",
			content: { "application/json": { schema: Upload } },
		},
		400: error,
		401: error,
		413: error,
		429: error,
		503: error,
	},
});
const list = createRoute({
	method: "get",
	path: "/",
	tags: ["owner-documents"],
	summary: "Owner lists the latest 30 document uploads",
	responses: {
		200: {
			description: "Uploads",
			content: { "application/json": { schema: z.object({ uploads: z.array(Upload) }) } },
		},
		401: error,
		429: error,
		503: error,
	},
});
const download = createRoute({
	method: "get",
	path: "/{id}",
	tags: ["owner-documents-internal"],
	summary: "PC downloads the immutable original document",
	request: { params: Id },
	responses: {
		200: {
			description: "Original document bytes",
			content: { "application/octet-stream": { schema: { type: "string", format: "binary" } } },
		},
		400: error,
		401: error,
		404: error,
		429: error,
		503: error,
	},
});
owner.openapi(create, async (c) => {
	if (!c.env.JOB_DB)
		return c.json(
			{ error: { code: "UNAVAILABLE", message: "Document uploads are not configured" } },
			503,
		);
	const auth = await adminCredentialStatus(c.req.raw, c.env);
	if (auth)
		return c.json({ error: { code: "UNAUTHORIZED", message: "Owner sign-in required" } }, auth);
	const meta = c.req.valid("query");
	const length = Number(c.req.header("Content-Length"));
	if (length > MAX_BYTES)
		return c.json(
			{ error: { code: "TOO_LARGE", message: "Documents must be at most 16 MiB" } },
			413,
		);
	const reader = c.req.raw.body?.getReader();
	const chunks: Uint8Array[] = [];
	let size = 0;
	if (reader) {
		while (true) {
			const { value, done } = await reader.read();
			if (done) break;
			size += value.byteLength;
			if (size > MAX_BYTES) {
				await reader.cancel();
				return c.json(
					{ error: { code: "TOO_LARGE", message: "Documents must be at most 16 MiB" } },
					413,
				);
			}
			chunks.push(value);
		}
	}
	const bytes = new Uint8Array(size);
	let offset = 0;
	for (const chunk of chunks) {
		bytes.set(chunk, offset);
		offset += chunk.length;
	}
	let valid = size > 0;
	if (meta.format === "pdf") valid &&= new TextDecoder().decode(bytes.slice(0, 5)) === "%PDF-";
	if (meta.format === "docx")
		valid &&= bytes[0] === 80 && bytes[1] === 75 && bytes[2] === 3 && bytes[3] === 4;
	if (meta.format === "txt") {
		try {
			const text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes);
			valid &&= !!text.trim() && !text.includes("\0") && text.length <= 2_000_000;
		} catch {
			valid = false;
		}
	}
	if (!valid)
		return c.json(
			{
				error: {
					code: "INVALID_DOCUMENT",
					message: "Use a text PDF, DOCX or nonempty UTF-8 TXT file",
				},
			},
			400,
		);
	const sha256 = Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)), (b) =>
		b.toString(16).padStart(2, "0"),
	).join("");
	const id = crypto.randomUUID();
	const source_id = `ownerupload:${id}`;
	const created_at = new Date().toISOString();
	const key = `owner-uploads/${id}/original.${meta.format}`;
	await c.env.R2_BUCKET.put(key, bytes, {
		httpMetadata: { contentType: "application/octet-stream" },
		customMetadata: { sha256 },
	});
	try {
		await c.env.JOB_DB.batch([
			c.env.JOB_DB.prepare(
				"INSERT INTO source_books(source_id,title,author,epub_url,updated_at,language,provenance) VALUES(?,?,?,?,?,?,?)",
			).bind(
				source_id,
				meta.title,
				meta.author,
				`https://openshelf.invalid/owner-uploads/${id}`,
				created_at,
				meta.language,
				JSON.stringify({
					upload_id: id,
					format: meta.format,
					sha256,
					rights_acknowledged: true,
					public_publication_acknowledged: true,
				}),
			),
			c.env.JOB_DB.prepare(
				"INSERT INTO owner_uploads(id,source_id,format,sha256,byte_size,created_at) VALUES(?,?,?,?,?,?)",
			).bind(id, source_id, meta.format, sha256, size, created_at),
		]);
	} catch (exc) {
		await c.env.R2_BUCKET.delete(key);
		throw exc;
	}
	return c.json(
		{
			id,
			source_id,
			title: meta.title,
			author: meta.author,
			language: meta.language,
			format: meta.format,
			created_at,
		},
		201,
		noStore,
	);
});
owner.openapi(list, async (c) => {
	if (!c.env.JOB_DB)
		return c.json(
			{ error: { code: "UNAVAILABLE", message: "Document uploads are not configured" } },
			503,
		);
	const auth = await adminCredentialStatus(c.req.raw, c.env);
	if (auth)
		return c.json({ error: { code: "UNAUTHORIZED", message: "Owner sign-in required" } }, auth);
	const result = await c.env.JOB_DB.prepare(
		"SELECT u.id,u.source_id,b.title,b.author,b.language,u.format,u.created_at,b.author_slug,b.title_slug,(SELECT j.id FROM generation_jobs j WHERE j.source_id=u.source_id AND j.stage<>'deleted' ORDER BY j.created_at DESC LIMIT 1) AS job_id FROM owner_uploads u JOIN source_books b ON b.source_id=u.source_id ORDER BY u.created_at DESC LIMIT 30",
	).all<z.infer<typeof Upload>>();
	return c.json({ uploads: result.results }, 200, noStore);
});
internal.openapi(download, async (c) => {
	if (!c.env.JOB_DB)
		return c.json(
			{ error: { code: "UNAVAILABLE", message: "Document uploads are not configured" } },
			503,
		);
	const auth = await credentialStatus(c.req.raw, c.env, c.env.PC_TOKEN);
	if (auth)
		return c.json({ error: { code: "UNAUTHORIZED", message: "PC credential required" } }, auth);
	const row = await c.env.JOB_DB.prepare("SELECT format FROM owner_uploads WHERE id=?")
		.bind(c.req.valid("param").id)
		.first<{ format: string }>();
	const object = row
		? await c.env.R2_BUCKET.get(`owner-uploads/${c.req.valid("param").id}/original.${row.format}`)
		: null;
	if (!object) return c.json({ error: { code: "NOT_FOUND", message: "Document not found" } }, 404);
	return new Response(object.body, {
		headers: {
			"Content-Type": "application/octet-stream",
			"Content-Length": String(object.size),
			...noStore,
		},
	});
});
export { owner, internal };
