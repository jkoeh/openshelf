import { mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, dirname, join, resolve } from "node:path";
import { getPlatformProxy, unstable_splitSqlQuery as splitSqlQuery, unstable_dev } from "wrangler";
import type { D1Database, R2Bucket } from "@cloudflare/workers-types";
import { sourceTitleSlug } from "../../src/utils/source-editions";

export const apiBase = "http://127.0.0.1:8788/api/v1";
export const pcToken = "browser-test-pc-token-longer-than-twenty-four";
export const ownerToken = "browser-test-owner-token-longer-than-twenty-four";
const workerRoot = resolve(__dirname, "../..");

export interface Edition {
  source_id: string;
  title: string;
  author: string;
  language: "en" | "zh";
  epub_url: string;
  labels: string[];
  texts: string[];
}

export interface ClaimedJob {
  id: string;
  source_id: string;
  language: "en" | "zh";
  build_id: string;
  lease_token: string;
  epub_sha256: string;
}

export async function api(path: string, body?: unknown, token?: string) {
  const response = await fetch(`${apiBase}${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (!response.ok) throw new Error(`${path}: ${response.status} ${await response.text()}`);
  return response.json();
}

export async function startHarness() {
  const state = await mkdtemp(join(tmpdir(), "openshelf-browser-"));
  const cleanState = async () => {
    if (dirname(resolve(state)) !== resolve(tmpdir()) || !basename(state).startsWith("openshelf-browser-")) {
      throw new Error("Refusing to remove a path outside the browser test temp directory");
    }
    await rm(state, { recursive: true, force: true });
  };
  const config = join(workerRoot, "tests/browser/wrangler.toml");
  const proxy = await getPlatformProxy<{ JOB_DB: D1Database; R2_BUCKET: R2Bucket }>({
    configPath: config, envFiles: [], remoteBindings: false, persist: { path: join(state, "v3") },
  });
  let server: Awaited<ReturnType<typeof unstable_dev>> | undefined;
  try {
    const migrations = (await readdir(join(workerRoot, "migrations"))).filter(name => name.endsWith(".sql")).sort();
    for (const name of migrations) {
      const sql = await readFile(join(workerRoot, "migrations", name), "utf8");
      await proxy.env.JOB_DB.batch(splitSqlQuery(sql).map(statement => proxy.env.JOB_DB.prepare(statement)));
    }
    server = await unstable_dev(join(workerRoot, "src/index.ts"), {
      config, envFiles: [], local: true, ip: "127.0.0.1", port: 8788,
      persistTo: state, logLevel: "error",
      experimental: { disableExperimentalWarning: true, disableDevRegistry: true, watch: false },
    });
    await proxy.env.R2_BUCKET.put("catalog.json", JSON.stringify({ version: 2, books: [] }));
  } catch (error) {
    await server?.stop();
    await proxy.dispose();
    await cleanState();
    throw error;
  }

  return {
    async cache(edition: Edition, epub: Uint8Array) {
      await api("/internal/source-books/sync", { books: [{ ...edition,
        provenance: { rights_statement: "Public domain in the USA.", source_url: edition.epub_url,
          rights_url: edition.epub_url, verified_at: "2026-10-01T00:00:00Z", fixture: true },
      }] }, pcToken);
      const response = await fetch(`${apiBase}/internal/source-books/${encodeURIComponent(edition.source_id)}/epub`, {
        method: "POST", headers: { Authorization: `Bearer ${pcToken}`, "Content-Type": "application/epub+zip" },
        body: Buffer.from(epub),
      });
      if (!response.ok) throw new Error(`Cache upload: ${response.status} ${await response.text()}`);
    },
    async claim(mandarin = false, documents = false): Promise<ClaimedJob | null> {
      const response = await api("/internal/generation-jobs/claim", { mandarin, expressive: false, documents }, pcToken) as { job: ClaimedJob | null };
      return response.job;
    },
    async fail(job: ClaimedJob) {
      await api(`/internal/generation-jobs/${job.id}/finish`, {
        lease_token: job.lease_token, success: false, error_code: "GENERATION_FAILED",
      }, pcToken);
    },
    async publish(edition: Edition, job: ClaimedJob, build = job.build_id, retained: string[] = []) {
      const rendition = edition.language === "zh" ? "qwen3-serena" : "kokoro-af-heart";
      const titleSlug = edition.title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
      const title = await sourceTitleSlug(titleSlug || "untitled", edition.source_id);
      const root = `books/openshelf/${title}`;
      const prefix = `${root}/audio/${rendition}/builds/${build}`;
      const audio = new Uint8Array(await readFile(join(workerRoot, "tests/browser/playback.m4a")));
      const sections = edition.texts.map((text, index) => ({ sequence: index + 1, section_type: "chapter",
        ordinal: index === 0 ? 10 : 2, display_label: edition.labels[index], display_title: "",
        filename: `section-${String(index + 1).padStart(2, "0")}.m4a`, duration_seconds: 12, word_count: text.length,
      }));
      const sectionData = sections.map((section, index) => {
        const text = edition.texts[index];
        const units = edition.language === "zh" ? text.match(/\p{Script=Han}|[\p{L}\p{N}]+/gu)! : text.match(/[\p{L}\p{N}]+/gu)!;
        return { sequence: section.sequence, section_type: section.section_type, ordinal: section.ordinal,
          heading: { display_label: section.display_label, display_title: "", spoken_text: section.display_label,
            words: [{ word: section.display_label, start: 0, end: 0.8 }] },
          chunks: [{ text, words: units.map((word, i) => ({ word, start: 1 + i, end: 1.8 + i })) }],
          word_count: units.length,
        };
      });
      for (const section of sections) await proxy.env.R2_BUCKET.put(`${prefix}/${section.filename}`, audio);
      await proxy.env.R2_BUCKET.put(`${prefix}/section_data.json`, JSON.stringify({ version: 2, rendition, build, sections: sectionData }));
      await proxy.env.R2_BUCKET.put(`${prefix}/rendition-manifest.json`, JSON.stringify({
        version: 2, rendition, build, voice: edition.language === "zh" ? "qwen3-serena" : "af_heart",
        engine: edition.language === "zh" ? "qwen3" : "kokoro", pipeline_version: "test-fixture",
        total_duration_seconds: 24, section_count: sections.length, sections,
      }));
      await proxy.env.R2_BUCKET.put(`${root}/manifest.json`, JSON.stringify({ title: edition.title,
        author: edition.author, source: edition.source_id.startsWith("ownerupload:") ? "local" : edition.source_id.startsWith("wikisource:") ? "wikisource" : "gutenberg",
        renditions: { [rendition]: { voice: edition.language === "zh" ? "qwen3-serena" : "af_heart",
          engine: edition.language === "zh" ? "qwen3" : "kokoro", display: edition.language === "zh" ? "Serena" : "Heart",
          current_build: build, available_builds: [build, ...retained] } },
      }));
      await proxy.env.R2_BUCKET.put("catalog.json", JSON.stringify({ version: 2, books: [{
        author: edition.author, author_slug: "openshelf", title: edition.title, title_slug: title,
        source: edition.source_id.startsWith("ownerupload:") ? "local" : edition.source_id.startsWith("wikisource:") ? "wikisource" : "gutenberg",
        rendition, current_build: build, total_duration_seconds: 24, section_count: 2,
      }] }));
      await api("/internal/catalog/sync", { books: [{ author_slug: "openshelf", title_slug: title }] }, pcToken);
      if (build === job.build_id) await api(`/internal/generation-jobs/${job.id}/finish`, {
        lease_token: job.lease_token, success: true, author_slug: "openshelf", title_slug: title,
      }, pcToken);
      return { author: "openshelf", title, rendition, build };
    },
    async stop() {
      await server!.stop();
      await proxy.dispose();
      await cleanState();
    },
  };
}
