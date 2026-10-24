import { type Page, expect, test } from "@playwright/test";
import { strToU8, zipSync } from "fflate";
import {
	type Edition,
	apiBase,
	ownerToken,
	startHarness,
} from "../../../worker/tests/browser/harness";

let harness: Awaited<ReturnType<typeof startHarness>>;
test.beforeAll(async () => {
	harness = await startHarness();
});
test.afterAll(async () => {
	await harness?.stop();
});

async function signInOwner(page: Page) {
	await page.route(`${apiBase}/admin/me`, async (route) => {
		expect(route.request().headers().authorization).toBe(`Bearer ${ownerToken}`);
		await route.fulfill({ json: { email: "johnkoeh@gmail.com" } });
	});
	await page.route("https://accounts.google.com/gsi/client", (route) =>
		route.fulfill({
			contentType: "application/javascript",
			body: `window.google={accounts:{id:{initialize:({callback})=>{window.ownerCallback=callback},renderButton:(element)=>{const button=document.createElement('button');button.textContent='Sign in with Google';button.onclick=()=>window.ownerCallback({credential:${JSON.stringify(ownerToken)}});element.appendChild(button)},disableAutoSelect:()=>{}}}};`,
		}),
	);
	await page.goto("/");
	await page.getByRole("button", { name: "Owner controls" }).click();
	await page.getByRole("button", { name: "Sign in with Google" }).click();
	await page.getByRole("button", { name: "Upload or paste text to audio" }).click();
}

for (const format of ["txt", "docx", "pdf"] as const) {
	test(`owner uploads ${format} and can cancel/retry extraction errors`, async ({ page }) => {
		await signInOwner(page);
		await page.getByRole("textbox", { name: "Document title" }).fill(`Owner ${format} document`);
		await page.getByRole("textbox", { name: "Document author" }).fill("OpenShelf");
		const bytes =
			format === "docx"
				? zipSync({
						"word/document.xml": strToU8(
							'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>File narration.</w:t></w:r></w:p></w:body></w:document>',
						),
					})
				: strToU8(
						format === "pdf"
							? "%PDF-1.4\nText-only fixture; extraction tested separately in Python."
							: "File narration.",
					);
		await page.getByLabel("Choose document file").setInputFiles({
			name: `document.${format}`,
			mimeType: "application/octet-stream",
			buffer: Buffer.from(bytes),
		});
		await page
			.getByRole("checkbox", {
				name: "I have distribution rights and agree to public audio and EPUB publication",
			})
			.click();
		const created = page.waitForResponse(
			(response) =>
				response.url() === `${apiBase}/generation-jobs` && response.request().method() === "POST",
		);
		await page.getByRole("button", { name: "Upload and generate audio" }).click();
		const job = await (await created).json();
		await expect(page.getByText("Audio: queued · queued")).toBeVisible();
		await page.getByRole("button", { name: "Cancel document audio" }).click();
		await expect(page.getByText("Audio: canceled · canceled")).toBeVisible();
		await page.getByRole("button", { name: "Retry document audio" }).click();
		await expect(page.getByText("Audio: queued · queued")).toBeVisible();
		const claimed = await harness.claim(false, true);
		expect(claimed?.id).toBe(job.id);
		const finished = await fetch(`${apiBase}/internal/generation-jobs/${job.id}/finish`, {
			method: "POST",
			headers: {
				Authorization: `Bearer ${"browser-test-pc-token-longer-than-twenty-four"}`,
				"Content-Type": "application/json",
			},
			body: JSON.stringify({
				lease_token: claimed?.lease_token,
				success: false,
				error_code: "InvalidOwnerDocument",
			}),
		});
		expect(finished.status).toBe(200);
		await expect(page.getByText(/The document could not be extracted/)).toBeVisible({
			timeout: 15000,
		});
	});
}

test("owner pastes Mandarin text, follows authenticated job and opens published audio", async ({
	page,
}) => {
	// Only the external Google identity exchange is stubbed. Upload, generation,
	// status, publication and media requests use the actual isolated Worker.
	await page.route(`${apiBase}/admin/me`, async (route) => {
		expect(route.request().headers().authorization).toBe(`Bearer ${ownerToken}`);
		await route.fulfill({ json: { email: "johnkoeh@gmail.com" } });
	});
	await page.route("https://accounts.google.com/gsi/client", (route) =>
		route.fulfill({
			contentType: "application/javascript",
			body: `window.google={accounts:{id:{initialize:({callback})=>{window.ownerCallback=callback},renderButton:(element)=>{const button=document.createElement('button');button.textContent='Sign in with Google';button.onclick=()=>window.ownerCallback({credential:${JSON.stringify(ownerToken)}});element.appendChild(button)},disableAutoSelect:()=>{}}}};`,
		}),
	);
	await page.goto("/");
	await expect(page.getByRole("button", { name: "Upload or paste text to audio" })).toHaveCount(0);
	await page.getByRole("button", { name: "Owner controls" }).click();
	await page.getByRole("button", { name: "Sign in with Google" }).click();
	await page.getByRole("button", { name: "Upload or paste text to audio" }).click();
	await page.getByRole("textbox", { name: "Document title" }).fill("Owner Mandarin document");
	await page.getByRole("textbox", { name: "Document author" }).fill("OpenShelf");
	await page.getByRole("button", { name: "Mandarin Chinese", exact: true }).click();
	await page
		.getByRole("textbox", { name: "Paste document text" })
		.fill("天地玄黃，Hello 世界！\n\n第二段，山水相逢。");
	await expect(page.getByRole("button", { name: "Upload and generate audio" })).toBeDisabled();
	await page
		.getByRole("checkbox", {
			name: "I have distribution rights and agree to public audio and EPUB publication",
		})
		.click();
	const created = page.waitForResponse(
		(response) =>
			response.url() === `${apiBase}/generation-jobs` && response.request().method() === "POST",
	);
	await page.getByRole("button", { name: "Upload and generate audio" }).click();
	const job = await (await created).json();
	await expect(page.getByText("Audio: queued · queued")).toBeVisible();
	expect((await fetch(`${apiBase}/generation-jobs/${job.id}`)).status).toBe(401);
	expect(await harness.claim(true)).toBeNull();
	const claimed = await harness.claim(true, true);
	expect(claimed?.source_id).toBe(job.source_id);
	expect(
		(await fetch(`${apiBase}/source-books/${encodeURIComponent(job.source_id)}/epub?inline=1`))
			.status,
	).toBe(400);
	const edition: Edition = {
		source_id: job.source_id,
		title: "Owner Mandarin document",
		author: "OpenShelf",
		language: "zh",
		epub_url: "https://openshelf.invalid/owner-document",
		labels: ["Text", "Second"],
		texts: ["天地玄黃，Hello 世界！", "第二段，山水相逢。"],
	};
	if (!claimed) throw new Error("Expected a claimed document job");
	await harness.publish(edition, claimed);
	await expect(page.getByRole("link", { name: "Listen to document", exact: true })).toBeVisible({
		timeout: 15000,
	});
	await page.getByRole("link", { name: "Listen to document", exact: true }).click();
	await expect(page).toHaveURL(/\/book\/openshelf\/owner-mandarin-document-upload-/);
	await page.getByText("Start Listening", { exact: true }).click();
	await expect(page.getByText("天地玄黃，Hello 世界！", { exact: true })).toBeVisible();
	await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
});
