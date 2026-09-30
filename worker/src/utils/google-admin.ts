import { type JWTVerifyGetKey, createRemoteJWKSet, jwtVerify } from "jose";
import type { Env } from "../types";
import { authorized } from "./job-auth";

const OWNER_EMAIL = "johnkoeh@gmail.com";
const googleKeys = createRemoteJWKSet(new URL("https://www.googleapis.com/oauth2/v3/certs"), {
	cacheMaxAge: 3_600_000,
	cooldownDuration: 30_000,
	timeoutDuration: 3_000,
});

export async function verifyGoogleIdToken(
	token: string,
	clientId: string,
	keys: JWTVerifyGetKey = googleKeys,
): Promise<boolean> {
	if (token.length < 100 || token.length > 4096 || !clientId) return false;
	try {
		const { payload } = await jwtVerify(token, keys, {
			audience: clientId,
			issuer: ["accounts.google.com", "https://accounts.google.com"],
			algorithms: ["RS256"],
			maxTokenAge: "1h",
			clockTolerance: 5,
			requiredClaims: ["sub", "iat", "exp", "email", "email_verified"],
		});
		return payload.email === OWNER_EMAIL && payload.email_verified === true;
	} catch {
		return false;
	}
}

export async function googleCredentialStatus(request: Request, env: Env): Promise<0 | 401 | 429> {
	if (!env.AUTH_RATE_LIMITER) return 429;
	const ip = request.headers.get("CF-Connecting-IP") ?? "unknown";
	if (!(await env.AUTH_RATE_LIMITER.limit({ key: `admin-google:${ip}` })).success) return 429;
	const header = request.headers.get("Authorization") ?? "";
	if (!header.startsWith("Bearer ") || !env.GOOGLE_CLIENT_ID) return 401;
	return (await verifyGoogleIdToken(header.slice(7), env.GOOGLE_CLIENT_ID)) ? 0 : 401;
}

export async function adminCredentialStatus(request: Request, env: Env): Promise<0 | 401 | 429> {
	if (await authorized(request, env.OWNER_TOKEN)) return 0;
	return googleCredentialStatus(request, env);
}
