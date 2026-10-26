import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
	ApiError,
	type CatalogParams,
	audioUrl,
	epubUrl,
	fetchBook,
	fetchBookBuilds,
	fetchCatalog,
	fetchSection,
	fetchSourceBooks,
	fetchSourceEpub,
	sourceEpubUrl,
	createGenerationJob,
	fetchGenerationJob,
	retryGenerationJob,
	cancelGenerationJob,
	regenerateGenerationJob,
	requestExpressiveGenerationJob,
	verifyAdminIdentity,
	fetchBookManagement,
	deleteBook,
} from "../../lib/api";

const mockFetch = vi.fn();

beforeEach(() => {
	mockFetch.mockReset();
	vi.stubGlobal("fetch", mockFetch);
});

describe("source discovery and owner jobs", () => {
	it("browses a bounded Chinese page without submitting an audio job", async () => {
		mockFetch.mockResolvedValueOnce(jsonResponse({ books: [], total: 20, page: 2, limit: 10 }));
		expect(await fetchSourceBooks("", undefined, "zh", 2)).toMatchObject({ total: 20, page: 2 });
		expect(mockFetch.mock.calls[0][0]).toBe(
			"http://localhost:8787/api/v1/source-books?q=&limit=10&language=zh&page=2",
		);
		expect(mockFetch).toHaveBeenCalledTimes(1);
	});
	it("queries only our database when an edition is missing", async () => {
		mockFetch.mockResolvedValueOnce(jsonResponse({ books: [] }));
		const result = await fetchSourceBooks("demons");
		expect(result.books).toEqual([]);
		expect(mockFetch).toHaveBeenCalledTimes(1);
		expect(mockFetch.mock.calls[0][0]).toBe(
			"http://localhost:8787/api/v1/source-books?q=demons&limit=10",
		);
		expect(mockFetch.mock.calls[0][1]).toEqual({ cache: "no-store" });
	});
	it("returns current indexed results without contacting an upstream service", async () => {
		const books = [{ source_id: "gutenberg:11", title: "Alice" }];
		mockFetch.mockResolvedValueOnce(jsonResponse({ books }));
		expect(await fetchSourceBooks("alice")).toEqual({ books });
		expect(mockFetch).toHaveBeenCalledTimes(1);
	});
	it("reads an unindexed exact EPUB directly after a missing cache response", async () => {
		mockFetch
			.mockResolvedValueOnce(errorResponse("NOT_FOUND", "Not indexed", 404))
			.mockResolvedValueOnce(
				new Response(new Uint8Array([80, 75, 3, 4]), {
					headers: { "Content-Type": "application/epub+zip" },
				}),
			);
		expect(
			await fetchSourceEpub(
				"standardebooks:fyodor-dostoevsky/demons/constance-garnett",
				new AbortController().signal,
			),
		).toEqual(new Uint8Array([80, 75, 3, 4]));
		expect(mockFetch.mock.calls[1][0]).toBe(
			"https://standardebooks.org/ebooks/fyodor-dostoevsky/demons/constance-garnett/downloads/fyodor-dostoevsky_demons_constance-garnett.epub?source=download",
		);
		expect(mockFetch.mock.calls[1][1]).toMatchObject({ credentials: "omit", redirect: "error" });
	});
	it("keeps the cached EPUB fast path and rejects malicious edition IDs before fetching", async () => {
		mockFetch.mockResolvedValueOnce(new Response(new Uint8Array([80, 75, 3, 4])));
		await fetchSourceEpub("standardebooks:author/book", new AbortController().signal);
		expect(mockFetch).toHaveBeenCalledTimes(1);
		await expect(
			fetchSourceEpub("standardebooks:author/../../other", new AbortController().signal),
		).rejects.toThrow("Invalid");
		expect(mockFetch).toHaveBeenCalledTimes(1);
	});
	it("does not retry upstream limits or fall back on our API rate limit", async () => {
		mockFetch.mockResolvedValueOnce(new Response("Limited", { status: 429 }));
		await expect(
			fetchSourceEpub("standardebooks:author/book", new AbortController().signal),
		).rejects.toThrow("limiting downloads");
		expect(mockFetch).toHaveBeenCalledTimes(1);
	});
	it("resolves and deletes a book only with the owner bearer token and no-store", async () => {
		mockFetch.mockImplementation(async () => jsonResponse({ deleted: true }));
		await fetchBookManagement("jane-austen", "pride-g1342", "google-id-token");
		await deleteBook("jane-austen", "pride-g1342", "google-id-token");
		expect(mockFetch.mock.calls[0][0]).toContain("/books/jane-austen/pride-g1342/management");
		expect(mockFetch.mock.calls[0][1].method).toBe("GET");
		expect(mockFetch.mock.calls[1][1].method).toBe("DELETE");
		for (const [, init] of mockFetch.mock.calls) {
			expect(init.headers.Authorization).toBe("Bearer google-id-token");
			expect(init.cache).toBe("no-store");
		}
	});
	it("builds an independent EPUB URL for the exact source ID", () => {
		expect(sourceEpubUrl("gutenberg:2554")).toBe(
			"http://localhost:8787/api/v1/source-books/gutenberg%3A2554/epub",
		);
		expect(mockFetch).not.toHaveBeenCalled();
	});
	it("encodes autocomplete text and uses no-store", async () => {
		mockFetch.mockResolvedValue(jsonResponse({ books: [] }));
		await fetchSourceBooks("Alice & Bob");
		expect(mockFetch.mock.calls[0][0]).toContain("q=Alice%20%26%20Bob");
		expect(mockFetch.mock.calls[0][1]).toEqual({ cache: "no-store" });
	});

	it("sends the exact source ID and owner bearer token only on job calls", async () => {
		mockFetch.mockImplementation(async () => jsonResponse({ id: "job-1" }));
		await createGenerationJob("gutenberg:11", "owner-secret");
		const [url, init] = mockFetch.mock.calls[0] as [string, RequestInit];
		expect(url).toContain("/generation-jobs");
		expect(init.method).toBe("POST");
		expect((init.headers as Record<string, string>).Authorization).toBe("Bearer owner-secret");
		expect(JSON.parse(init.body as string)).toEqual({ source_id: "gutenberg:11" });
		await fetchGenerationJob("job-1", "owner-secret");
		expect(mockFetch.mock.calls[1][0]).toContain("/generation-jobs/job-1");
		await retryGenerationJob("job-1", "owner-secret");
		expect(mockFetch.mock.calls[2][0]).toContain("/generation-jobs/job-1/retry");
	});

	it("creates and reads a public job without an authorization header", async () => {
		mockFetch.mockImplementation(async () => jsonResponse({ id: "job-1" }));
		await createGenerationJob("gutenberg:11");
		const createInit = mockFetch.mock.calls[0][1] as RequestInit;
		expect(createInit.headers).toEqual({ "Content-Type": "application/json" });
		await fetchGenerationJob("job-1");
		const statusInit = mockFetch.mock.calls[1][1] as RequestInit;
		expect(statusInit.headers).toBeUndefined();
	});

	it("sends browser owner credentials only to admin actions", async () => {
		mockFetch.mockImplementation(async () => jsonResponse({ id: "job-1" }));
		await verifyAdminIdentity("google-id-token");
		await cancelGenerationJob("job-1", "google-id-token");
		await regenerateGenerationJob("gutenberg:11", "google-id-token");
		await requestExpressiveGenerationJob("gutenberg:11", "google-id-token", true);
		expect(mockFetch.mock.calls[0][0]).toContain("/admin/me");
		expect(mockFetch.mock.calls[1][0]).toContain("/generation-jobs/job-1/cancel");
		expect(JSON.parse(mockFetch.mock.calls[2][1].body)).toEqual({
			source_id: "gutenberg:11",
			regenerate: true,
		});
		expect(JSON.parse(mockFetch.mock.calls[3][1].body)).toEqual({
			source_id: "gutenberg:11",
			mode: "expressive",
			regenerate: true,
		});
		for (const [, init] of mockFetch.mock.calls) {
			expect(init.headers.Authorization).toBe("Bearer google-id-token");
			expect(init.cache).toBe("no-store");
		}
	});
});

function jsonResponse(data: unknown, status = 200) {
	return new Response(JSON.stringify(data), {
		status,
		headers: { "Content-Type": "application/json" },
	});
}

function errorResponse(code: string, message: string, status: number) {
	return new Response(JSON.stringify({ error: { code, message } }), {
		status,
		headers: { "Content-Type": "application/json" },
	});
}

describe("fetchCatalog", () => {
	it("fetches catalog without params", async () => {
		const catalog = { version: 1, generated_at: "2025-01-01", books: [] };
		mockFetch.mockResolvedValue(jsonResponse(catalog));

		const result = await fetchCatalog();

		expect(result).toEqual(catalog);
		expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining("/catalog"), {
			cache: "no-store",
		});
		expect(mockFetch.mock.calls[0][0]).not.toContain("?");
	});

	it("appends search params", async () => {
		mockFetch.mockResolvedValue(jsonResponse({ version: 1, books: [] }));

		await fetchCatalog({ q: "kafka", page: 2, limit: 10 });

		const url: string = mockFetch.mock.calls[0][0];
		expect(url).toContain("q=kafka");
		expect(url).toContain("page=2");
		expect(url).toContain("limit=10");
	});

	it("skips undefined params", async () => {
		mockFetch.mockResolvedValue(jsonResponse({ version: 1, books: [] }));

		await fetchCatalog({ q: "test" });

		const url: string = mockFetch.mock.calls[0][0];
		expect(url).toContain("q=test");
		expect(url).not.toContain("page");
		expect(url).not.toContain("limit");
	});
});

describe("fetchBook", () => {
	it("fetches manifest", async () => {
		const manifest = {
			title: "The Trial",
			author: "Franz Kafka",
			source: "gutenberg",
			renditions: {},
		};
		mockFetch.mockResolvedValue(jsonResponse(manifest));

		const result = await fetchBook("franz-kafka", "the-trial");

		expect(result.title).toBe("The Trial");
		expect(mockFetch.mock.calls[0][0]).toContain("/books/franz-kafka/the-trial");
	});
});

describe("fetchBookBuilds", () => {
	it("fetches build selections", async () => {
		const builds = {
			title: "The Trial",
			author: "Franz Kafka",
			source: "gutenberg",
			renditions: {},
		};
		mockFetch.mockResolvedValue(jsonResponse(builds));

		const result = await fetchBookBuilds("franz-kafka", "the-trial");

		expect(result.title).toBe("The Trial");
		expect(mockFetch.mock.calls[0][0]).toContain("/books/franz-kafka/the-trial/builds");
		expect(mockFetch.mock.calls[0][1]).toEqual({ cache: "no-store" });
	});
});

describe("fetchSection", () => {
	it("fetches section heading, text, and timestamps", async () => {
		const section = {
			sequence: 1,
			section_type: "chapter",
			ordinal: 1,
			heading: {
				display_label: "I",
				display_title: "Down the Rabbit-Hole",
				spoken_text: "Chapter One. Down the Rabbit-Hole.",
			},
			chunks: ["Hello"],
			word_count: 1,
			words: [],
		};
		mockFetch.mockResolvedValue(jsonResponse(section));

		const result = await fetchSection(
			"franz-kafka",
			"the-trial",
			1,
			"kokoro-af-heart",
			"2a4f9c1b3d8e7f60",
		);

		expect(result.sequence).toBe(1);
		expect(result.heading.display_label).toBe("I");
		expect(result.chunks).toEqual(["Hello"]);
		expect(mockFetch.mock.calls[0][0]).toContain("/sections/1");
		expect(mockFetch.mock.calls[0][0]).toContain("rendition=kokoro-af-heart");
		expect(mockFetch.mock.calls[0][0]).toContain("build=2a4f9c1b3d8e7f60");
	});
});

describe("error handling", () => {
	it("throws ApiError on 404 with JSON body", async () => {
		mockFetch.mockResolvedValue(errorResponse("NOT_FOUND", "Book not found", 404));

		try {
			await fetchBook("nobody", "nothing");
			expect.unreachable("should have thrown");
		} catch (e) {
			expect(e).toBeInstanceOf(ApiError);
			const err = e as ApiError;
			expect(err.status).toBe(404);
			expect(err.code).toBe("NOT_FOUND");
			expect(err.message).toBe("Book not found");
		}
	});

	it("throws ApiError on 500 with non-JSON body", async () => {
		mockFetch.mockResolvedValue(
			new Response("Internal Server Error", { status: 500, statusText: "Internal Server Error" }),
		);

		await expect(fetchBook("a", "b")).rejects.toThrow(ApiError);
	});

	it("throws ApiError on 400", async () => {
		mockFetch.mockResolvedValue(errorResponse("INVALID_PARAM", "Invalid slug", 400));

		await expect(fetchBook("INVALID", "foo")).rejects.toThrow(ApiError);
	});
});

describe("URL builders", () => {
	it("audioUrl addresses a section", () => {
		const url = audioUrl("franz-kafka", "the-trial", 1, "kokoro-af-heart", "2a4f9c1b3d8e7f60");
		expect(url).toContain("/sections/1/audio");
		expect(url).toContain("rendition=kokoro-af-heart");
		expect(url).toContain("build=2a4f9c1b3d8e7f60");
	});

	it("audioUrl handles double-digit sections", () => {
		const url = audioUrl("franz-kafka", "the-trial", 12, "kokoro-af-heart", "2a4f9c1b3d8e7f60");
		expect(url).toContain("/sections/12/audio");
	});

	it("epubUrl builds correct path", () => {
		const url = epubUrl("franz-kafka", "the-trial");
		expect(url).toContain("/books/franz-kafka/the-trial/epub");
	});
});
