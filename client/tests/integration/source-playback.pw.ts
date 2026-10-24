import { createHash } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import { strToU8, zipSync } from "fflate";
import { api, apiBase, ownerToken, startHarness, type Edition } from "../../../worker/tests/browser/harness";

declare global {
  interface Window { __testMedia: HTMLMediaElement[] }
}

const chinese: Edition = {
  source_id: "wikisource:zh:1:10:1234567890abcdef", language: "zh", title: "古書測試", author: "OpenShelf",
  epub_url: "https://zh.wikisource.org/w/index.php?oldid=10",
  labels: ["第十回", "第二回"], texts: ["天地玄黃，Hello 世界！", "第二段，山水相逢。"],
};
const english: Edition = {
  source_id: "gutenberg:11", language: "en", title: "English fixture", author: "OpenShelf",
  epub_url: "https://www.gutenberg.org/ebooks/11.epub3.images",
  labels: ["Chapter X", "Chapter II"], texts: ["Hello, world. Reading stays available.", "Second section follows the spine."],
};

function readingEpub(edition: Edition) {
  const files: Record<string, string> = {
    mimetype: "application/epub+zip",
    "META-INF/container.xml": '<container><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>',
    // Filenames and chapter ordinals deliberately disagree with spine order.
    "book.opf": `<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">${edition.source_id.startsWith("wikisource:") ? `urn:openshelf:${edition.source_id}` : "https://www.gutenberg.org/ebooks/11"}</dc:identifier><dc:title>${edition.title}</dc:title><dc:creator>${edition.author}</dc:creator><dc:language>${edition.language}</dc:language><dc:rights>Public domain in the USA.</dc:rights></metadata><manifest><item id="two" href="02.xhtml" media-type="application/xhtml+xml"/><item id="ten" href="10.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="ten"/><itemref idref="two"/></spine></package>`,
    "10.xhtml": `<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>${edition.labels[0]}</h1><p>${edition.texts[0]}</p></body></html>`,
    "02.xhtml": `<html xmlns="http://www.w3.org/1999/xhtml"><body><h1>${edition.labels[1]}</h1><p>${edition.texts[1]}</p></body></html>`,
  };
  return zipSync(Object.fromEntries(Object.entries(files).map(([name, text]) => [name, strToU8(text)])));
}

async function media(page: Page) {
  return page.evaluate(() => {
    const audio = window.__testMedia.at(-1);
    return audio ? { time: audio.currentTime, paused: audio.paused, duration: audio.duration,
      src: audio.currentSrc, error: audio.error?.message ?? null } : null;
  });
}

let harness: Awaited<ReturnType<typeof startHarness>>;
test.beforeAll(async () => { harness = await startHarness(); });
test.afterAll(async () => { await harness?.stop(); });

for (const edition of [chinese, english]) {
  test(`${edition.language}: cached reading survives failed audio and opens coherent published playback`, async ({ page }) => {
    const epub = readingEpub(edition);
    await harness.cache(edition, epub);
    const upstreamRequests: string[] = [];
    await page.route(/https:\/\/(?:zh\.wikisource\.org|(?:www\.)?gutenberg\.(?:org|pglaf\.org))\//, route => {
      upstreamRequests.push(route.request().url());
      return route.abort();
    });
    // Observe the real browser media object. No fake audio clock, codec or player.
    await page.addInitScript(() => {
      window.__testMedia = [];
      const play = HTMLMediaElement.prototype.play;
      HTMLMediaElement.prototype.play = function () {
        if (!window.__testMedia.includes(this)) window.__testMedia.push(this);
        return play.call(this);
      };
    });
    const audioRequests: string[] = [];
    page.on("request", request => {
      if (request.url().includes("/sections/") && request.url().includes("/audio?")) audioRequests.push(request.url());
    });
    await page.goto("/");
    await page.getByRole("textbox", { name: "Search books" }).fill(edition.title);
    await expect(page.getByRole("button", { name: "Read now" })).toBeVisible();
    const created = page.waitForResponse(response => response.url() === `${apiBase}/generation-jobs` && response.request().method() === "POST");
    await page.getByRole("button", { name: "Read now" }).click();
    const job = await (await created).json();
    const text = page.getByText(edition.texts[0], { exact: true });
    await expect(text).toBeVisible();
    // Exercise the shareable/follow-existing-job reader URL as well as creation.
    const readingUrl = new URL(page.url());
    readingUrl.searchParams.set("job", job.id);
    await page.goto(readingUrl.toString());
    await expect(text).toBeVisible();
    await expect(page.getByText(edition.labels[0], { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Next section", exact: true }).click();
    await expect(page.getByText(edition.labels[1], { exact: true })).toBeVisible();
    await expect(page.getByText(edition.texts[1], { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Previous section", exact: true }).click();
    await expect(text).toBeVisible();
    const cached = await fetch(`${apiBase}/source-books/${encodeURIComponent(edition.source_id)}/epub?inline=1`);
    expect(cached.status).toBe(200);
    const bytes = new Uint8Array(await cached.arrayBuffer());
    expect(createHash("sha256").update(bytes).digest("hex")).toBe(createHash("sha256").update(epub).digest("hex"));
    if (edition.language === "zh") expect(await harness.claim()).toBeNull();
    const claimed = await harness.claim(edition.language === "zh");
    expect(claimed).toMatchObject({ id: job.id, language: edition.language,
      epub_sha256: createHash("sha256").update(epub).digest("hex") });
    await harness.fail(claimed!);
    await expect(page.getByText("Audio creation stopped. You can keep reading.", { exact: true })).toBeVisible({ timeout: 20_000 });
    await expect(text).toBeVisible();
    await api(`/generation-jobs/${job.id}/retry`, {}, ownerToken);
    const retried = await harness.claim(edition.language === "zh");
    expect(retried?.build_id).toBe(claimed!.build_id);
    const publication = await harness.publish(edition, retried!);
    // Reload follows the same job and must not submit a duplicate start.
    await page.reload();
    await expect(page.getByRole("button", { name: "Start Listening" })).toBeVisible();
    await page.getByRole("button", { name: "Start Listening" }).click();
    await expect(page).toHaveURL(/\/read\/openshelf\//);
    await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
    await expect.poll(async () => (await media(page))?.time ?? 0).toBeGreaterThan(0.1);
    expect((await media(page))?.error).toBeNull();
    expect((await media(page))?.duration).toBeGreaterThan(11);
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    await expect.poll(async () => (await media(page))?.paused).toBe(true);
    const word = page.getByText(edition.language === "zh" ? "Hello" : "world", { exact: true });
    await expect(word.locator("..")).toHaveText(edition.texts[0]);
    await word.click();
    const seekTime = edition.language === "zh" ? 5 : 2;
    await expect.poll(async () => (await media(page))?.time ?? 0).toBeGreaterThanOrEqual(seekTime);
    await expect(word).not.toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    await page.getByRole("button", { name: "Pause", exact: true }).click();
    // Publishing another build while reading must not switch this session.
    await harness.publish(edition, retried!, "fedcba9876543210", [publication.build]);
    const sectionBase = `${apiBase}/books/${publication.author}/${publication.title}/sections/1`;
    const range = await fetch(`${sectionBase}/audio?rendition=${publication.rendition}&build=${publication.build}`, { headers: { Range: "bytes=0-31" } });
    expect(range.status).toBe(206);
    expect((await range.arrayBuffer()).byteLength).toBe(32);
    expect((await fetch(`${sectionBase}?rendition=${publication.rendition}&build=0000000000000000`)).status).toBe(404);
    // Native ended event must advance to the next source section and load its audio.
    await page.getByRole("button", { name: "Play", exact: true }).click();
    await page.getByText("+15", { exact: true }).click();
    await expect(page.getByText(edition.texts[1], { exact: true })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeVisible();
    await expect.poll(async () => (await media(page))?.src ?? "").toContain("/sections/2/audio?");
    expect(audioRequests.length).toBeGreaterThanOrEqual(2);
    for (const request of audioRequests) {
      const url = new URL(request);
      expect(url.searchParams.get("rendition")).toBe(publication.rendition);
      expect(url.searchParams.get("build")).toBe(publication.build);
    }
    expect(upstreamRequests).toEqual([]);
  });
}
