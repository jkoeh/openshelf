import { API_BASE } from "../constants/config";
import type {
	BookBuildsResponse,
	CatalogResponse,
	SectionResponse,
	Manifest,
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

export function fetchCatalog(params: CatalogParams = {}): Promise<CatalogResponse> {
	const search = new URLSearchParams();
	if (params.q) search.set("q", params.q);
	if (params.author) search.set("author", params.author);
	if (params.page) search.set("page", String(params.page));
	if (params.limit) search.set("limit", String(params.limit));
	if (params.sort) search.set("sort", params.sort);
	const qs = search.toString();
	return fetchJson<CatalogResponse>(`${API_BASE}/catalog${qs ? `?${qs}` : ""}`);
}

export function fetchBook(author: string, title: string): Promise<Manifest> {
	return fetchJson<Manifest>(`${API_BASE}/books/${author}/${title}`);
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
