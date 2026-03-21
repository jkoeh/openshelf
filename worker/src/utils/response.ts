import { CACHE_IMMUTABLE, CACHE_SHORT } from "../constants";

export function jsonResponse(data: unknown, cacheControl = CACHE_SHORT, status = 200): Response {
	return new Response(JSON.stringify(data), {
		status,
		headers: {
			"Content-Type": "application/json",
			"Cache-Control": cacheControl,
		},
	});
}

export function immutableJsonResponse(data: unknown): Response {
	return jsonResponse(data, CACHE_IMMUTABLE);
}

export function errorResponse(code: string, message: string, status: number): Response {
	return jsonResponse({ error: { code, message } }, "no-store", status);
}

export function notFound(message: string): Response {
	return errorResponse("NOT_FOUND", message, 404);
}

export function badRequest(message: string): Response {
	return errorResponse("INVALID_PARAM", message, 400);
}
