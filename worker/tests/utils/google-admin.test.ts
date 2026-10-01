import { SignJWT, createLocalJWKSet, exportJWK, generateKeyPair } from "jose";
import { describe, expect, it } from "vitest";
import { verifyGoogleIdToken } from "../../src/utils/google-admin";

const clientId = "openshelf-test.apps.googleusercontent.com";

describe("Google admin identity", () => {
	it("requires a valid Google signature and exact verified owner identity", async () => {
		const { publicKey, privateKey } = await generateKeyPair("RS256");
		const jwk = { ...(await exportJWK(publicKey)), kid: "test-key", alg: "RS256", use: "sig" };
		const keys = createLocalJWKSet({ keys: [jwk] });
		const token = (changes: Record<string, unknown> = {}) =>
			new SignJWT({ email: "johnkoeh@gmail.com", email_verified: true, ...changes })
				.setProtectedHeader({ alg: "RS256", kid: "test-key" })
				.setIssuer("https://accounts.google.com")
				.setAudience(clientId)
				.setSubject("google-owner-id")
				.setIssuedAt()
				.setExpirationTime("1h")
				.sign(privateKey);
		expect(await verifyGoogleIdToken(await token(), clientId, keys)).toBe(true);
		expect(
			await verifyGoogleIdToken(await token({ email: "other@gmail.com" }), clientId, keys),
		).toBe(false);
		expect(await verifyGoogleIdToken(await token({ email_verified: false }), clientId, keys)).toBe(
			false,
		);
		expect(await verifyGoogleIdToken(await token(), "wrong-client", keys)).toBe(false);
		const badIssuer = await new SignJWT({ email: "johnkoeh@gmail.com", email_verified: true })
			.setProtectedHeader({ alg: "RS256", kid: "test-key" })
			.setIssuer("https://evil.example")
			.setAudience(clientId)
			.setSubject("google-owner-id")
			.setIssuedAt()
			.setExpirationTime("1h")
			.sign(privateKey);
		expect(await verifyGoogleIdToken(badIssuer, clientId, keys)).toBe(false);
		const expired = await new SignJWT({ email: "johnkoeh@gmail.com", email_verified: true })
			.setProtectedHeader({ alg: "RS256", kid: "test-key" })
			.setIssuer("https://accounts.google.com")
			.setAudience(clientId)
			.setSubject("google-owner-id")
			.setIssuedAt(Math.floor(Date.now() / 1000) - 7200)
			.setExpirationTime(Math.floor(Date.now() / 1000) - 3600)
			.sign(privateKey);
		expect(await verifyGoogleIdToken(expired, clientId, keys)).toBe(false);
		expect(await verifyGoogleIdToken(`${await token()}x`, clientId, keys)).toBe(false);
	});
});
