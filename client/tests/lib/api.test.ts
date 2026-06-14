import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
	ApiError,
	type CatalogParams,
	audioUrl,
	epubUrl,
	fetchBook,
	fetchBookBuilds,
	fetchCatalog,
	fetchChapter,
} from "../../lib/api";

const mockFetch = vi.fn();

beforeEach(() => {
	mockFetch.mockReset();
	vi.stubGlobal("fetch", mockFetch);
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
		expect(mockFetch).toHaveBeenCalledWith(expect.stringContaining("/catalog"));
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

describe("fetchChapter", () => {
	it("fetches chapter text", async () => {
		const chapter = { number: 1, title: "Chapter 1", chunks: ["Hello"], word_count: 1 };
		mockFetch.mockResolvedValue(jsonResponse(chapter));

		const result = await fetchChapter(
			"franz-kafka",
			"the-trial",
			1,
			"kokoro-af-heart",
			"2a4f9c1b3d8e7f60",
		);

		expect(result.number).toBe(1);
		expect(result.chunks).toEqual(["Hello"]);
		expect(mockFetch.mock.calls[0][0]).toContain("/chapters/1");
		expect(mockFetch.mock.calls[0][0]).toContain("rendition=kokoro-af-heart");
		expect(mockFetch.mock.calls[0][0]).toContain("build=2a4f9c1b3d8e7f60");
	});
});

describe("error handling", () => {
	it("throws ApiError on 404 with JSON body", async () => {
		mockFetch.mockResolvedValue(
			errorResponse("NOT_FOUND", "Book not found", 404),
		);

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
		mockFetch.mockResolvedValue(
			errorResponse("INVALID_PARAM", "Invalid slug", 400),
		);

		await expect(fetchBook("INVALID", "foo")).rejects.toThrow(ApiError);
	});
});

describe("URL builders", () => {
	it("audioUrl pads chapter number", () => {
		const url = audioUrl(
			"franz-kafka",
			"the-trial",
			1,
			"kokoro-af-heart",
			"2a4f9c1b3d8e7f60",
		);
		expect(url).toContain("/audio/01");
		expect(url).toContain("rendition=kokoro-af-heart");
		expect(url).toContain("build=2a4f9c1b3d8e7f60");
	});

	it("audioUrl handles double-digit chapters", () => {
		const url = audioUrl(
			"franz-kafka",
			"the-trial",
			12,
			"kokoro-af-heart",
			"2a4f9c1b3d8e7f60",
		);
		expect(url).toContain("/audio/12");
	});

	it("epubUrl builds correct path", () => {
		const url = epubUrl("franz-kafka", "the-trial");
		expect(url).toContain("/books/franz-kafka/the-trial/epub");
	});
});
