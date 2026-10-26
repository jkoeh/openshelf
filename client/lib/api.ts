import { API_BASE } from "../constants/config";
import { readBoundedSource, standardEbooksEpubUrl } from "./standard-ebooks";
import type {
	BookBuildsResponse,
	SourceBook,
	GenerationJob,
	CatalogResponse,
	SectionResponse,
	Manifest,
	BookManagement,
} from "../types";

class ApiError extends Error {
	constructor(
		public status: number,
		public code: string,
		message: string,
	) {
		super(message);
		this.name = "ApiError";
	}
}

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
	const res = init ? await fetch(url, init) : await fetch(url);
	if (!res.ok) {
		let code = "UNKNOWN";
		let message = res.statusText;
		try {
			const body = await res.json();
			if (body?.error) {
				code = body.error.code ?? code;
				message = body.error.message ?? message;
			}
		} catch {
			// response wasn't JSON, keep defaults
		}
		throw new ApiError(res.status, code, message);
	}
	return res.json() as Promise<T>;
}

export interface CatalogParams {
	q?: string;
	author?: string;
	page?: number;
	limit?: number;
	sort?: string;
}

export function fetchCatalog(params: CatalogParams = {}, _fresh = false): Promise<CatalogResponse> {
	const search = new URLSearchParams();
	if (params.q) search.set("q", params.q);
	if (params.author) search.set("author", params.author);
	if (params.page) search.set("page", String(params.page));
	if (params.limit) search.set("limit", String(params.limit));
	if (params.sort) search.set("sort", params.sort);
	const qs = search.toString();
	return fetchJson<CatalogResponse>(`${API_BASE}/catalog${qs ? `?${qs}` : ""}`, {
		cache: "no-store",
	});
}

export async function fetchSourceBooks(
	q: string,
	signal?: AbortSignal,
	language?: "en" | "zh",
	page?: number,
): Promise<{ books: SourceBook[]; total?: number; page?: number; limit?: number }> {
	return fetchJson(
		`${API_BASE}/source-books?q=${encodeURIComponent(q)}&limit=10${language ? `&language=${language}` : ""}${page ? `&page=${page}` : ""}`,
		{ cache: "no-store", ...(signal ? { signal } : {}) },
	);
}

export function sourceEpubUrl(sourceId: string): string {
	return `${API_BASE}/source-books/${encodeURIComponent(sourceId)}/epub`;
}

export async function fetchSourceEpub(sourceId: string, signal: AbortSignal): Promise<Uint8Array> {
	const direct = sourceId.startsWith("standardebooks:") ? standardEbooksEpubUrl(sourceId) : null;
	let response = await fetch(`${sourceEpubUrl(sourceId)}?inline=1`, { signal });
	if (direct && [404, 502].includes(response.status)) {
		await response.body?.cancel();
		response = await fetch(direct, { signal, credentials: "omit", redirect: "error" });
	}
	if (!response.ok) {
		await response.body?.cancel();
		throw new Error(
			response.status === 429
				? "Standard Ebooks is limiting downloads. Try again later."
				: "Could not load the book. Try again shortly.",
		);
	}
	const type = response.headers.get("Content-Type") ?? "";
	if (type.includes("text/html") || type.includes("application/xhtml")) {
		await response.body?.cancel();
		throw new Error("The source returned a web page instead of an EPUB. Try again later.");
	}
	return readBoundedSource(response, 32 * 1024 * 1024);
}

function ownerRequest<T>(path: string, token: string, body?: object): Promise<T> {
	return fetchJson<T>(`${API_BASE}${path}`, {
		method: body ? "POST" : "GET",
		headers: {
			Authorization: `Bearer ${token}`,
			...(body ? { "Content-Type": "application/json" } : {}),
		},
		...(body ? { body: JSON.stringify(body) } : {}),
		cache: "no-store",
	});
}

export interface OwnerUpload {
	id: string;
	source_id: string;
	title: string;
	author: string;
	language: "en" | "zh";
	format: "pdf" | "docx" | "txt";
	created_at: string;
	author_slug?: string | null;
	title_slug?: string | null;
	job_id?: string | null;
}

export function fetchOwnerUploads(token: string): Promise<{ uploads: OwnerUpload[] }> {
	return ownerRequest("/admin/uploads", token);
}

export function uploadOwnerDocument(
	token: string,
	metadata: {
		title: string;
		author: string;
		language: "en" | "zh";
		format: "pdf" | "docx" | "txt";
	},
	bytes: Blob,
): Promise<OwnerUpload> {
	const query = new URLSearchParams({ ...metadata, publish: "yes" });
	return fetchJson<OwnerUpload>(`${API_BASE}/admin/uploads?${query}`, {
		method: "POST",
		headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/octet-stream" },
		body: bytes,
		cache: "no-store",
	});
}

export function createGenerationJob(sourceId: string, token?: string): Promise<GenerationJob> {
	return fetchJson<GenerationJob>(`${API_BASE}/generation-jobs`, {
		method: "POST",
		headers: {
			"Content-Type": "application/json",
			...(token ? { Authorization: `Bearer ${token}` } : {}),
		},
		body: JSON.stringify({ source_id: sourceId }),
		cache: "no-store",
	});
}

export function fetchGenerationJob(id: string, token?: string): Promise<GenerationJob> {
	return fetchJson<GenerationJob>(`${API_BASE}/generation-jobs/${encodeURIComponent(id)}`, {
		...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
		cache: "no-store",
	});
}

export function retryGenerationJob(id: string, token: string): Promise<GenerationJob> {
	return ownerRequest(`/generation-jobs/${encodeURIComponent(id)}/retry`, token, {});
}

export function cancelGenerationJob(id: string, token: string): Promise<GenerationJob> {
	return ownerRequest(`/generation-jobs/${encodeURIComponent(id)}/cancel`, token, {});
}

export function regenerateGenerationJob(sourceId: string, token: string): Promise<GenerationJob> {
	return ownerRequest("/generation-jobs", token, { source_id: sourceId, regenerate: true });
}

export function requestExpressiveGenerationJob(
	sourceId: string,
	token: string,
	regenerate: boolean,
): Promise<GenerationJob> {
	return ownerRequest("/generation-jobs", token, {
		source_id: sourceId,
		mode: "expressive",
		regenerate,
	});
}

export function verifyAdminIdentity(token: string): Promise<{ email: string }> {
	return ownerRequest("/admin/me", token);
}

export function fetchBook(author: string, title: string, fresh = false): Promise<Manifest> {
	return fetchJson<Manifest>(
		`${API_BASE}/books/${author}/${title}`,
		fresh ? { cache: "no-store" } : undefined,
	);
}

export function fetchBookManagement(
	author: string,
	title: string,
	token: string,
): Promise<BookManagement> {
	return ownerRequest(
		`/books/${encodeURIComponent(author)}/${encodeURIComponent(title)}/management`,
		token,
	);
}

export function deleteBook(
	author: string,
	title: string,
	token: string,
): Promise<{ deleted: true }> {
	return fetchJson(`${API_BASE}/books/${encodeURIComponent(author)}/${encodeURIComponent(title)}`, {
		method: "DELETE",
		headers: { Authorization: `Bearer ${token}` },
		cache: "no-store",
	});
}

export function fetchBookBuilds(author: string, title: string): Promise<BookBuildsResponse> {
	return fetchJson<BookBuildsResponse>(`${API_BASE}/books/${author}/${title}/builds`, {
		cache: "no-store",
	});
}

export function fetchSection(
	author: string,
	title: string,
	sequence: number,
	rendition: string,
	build: string,
): Promise<SectionResponse> {
	const search = new URLSearchParams({ rendition, build });
	return fetchJson<SectionResponse>(
		`${API_BASE}/books/${author}/${title}/sections/${sequence}?${search}`,
	);
}

export function audioUrl(
	author: string,
	title: string,
	sequence: number,
	rendition: string,
	build: string,
): string {
	const search = new URLSearchParams({ rendition, build });
	return `${API_BASE}/books/${author}/${title}/sections/${sequence}/audio?${search}`;
}

export function coverUrl(author: string, title: string): string {
	return `${API_BASE}/books/${author}/${title}/cover`;
}

export function epubUrl(author: string, title: string): string {
	return `${API_BASE}/books/${author}/${title}/epub`;
}

export { ApiError };
