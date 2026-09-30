import type { Env } from "../types";

async function equalSecrets(a: string, b: string): Promise<boolean> {
	const encoder = new TextEncoder();
	const [left, right] = await Promise.all([
		crypto.subtle.digest("SHA-256", encoder.encode(a)),
		crypto.subtle.digest("SHA-256", encoder.encode(b)),
	]);
	const x = new Uint8Array(left);
	const y = new Uint8Array(right);
	let diff = 0;
	for (let i = 0; i < x.length; i++) diff |= x[i] ^ y[i];
	return diff === 0;
}

export async function authorized(request: Request, secret: string | undefined): Promise<boolean> {
	if (!secret || secret.length < 24) return false;
	const header = request.headers.get("Authorization") ?? "";
	if (!header.startsWith("Bearer ") || header.length > 256) return false;
	return equalSecrets(header.slice(7), secret);
}

export async function credentialStatus(
	request: Request,
	env: Env,
	secret: string | undefined,
): Promise<0 | 401 | 429> {
	if (await authorized(request, secret)) return 0;
	if (!env.AUTH_RATE_LIMITER) return 429;
	const ip = request.headers.get("CF-Connecting-IP") ?? "unknown";
	return (await env.AUTH_RATE_LIMITER.limit({ key: `auth:${ip}` })).success ? 401 : 429;
}

export const noStore = { "Cache-Control": "no-store" };
