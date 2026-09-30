import { Link } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, Text, TextInput, View } from "react-native";
import { useTheme } from "../hooks/useTheme";
import { createGenerationJob, fetchGenerationJob, fetchSourceBooks, retryGenerationJob } from "../lib/api";
import type { GenerationJob, SourceBook } from "../types";

function savedToken(): string {
  try { return typeof sessionStorage === "undefined" ? "" : sessionStorage.getItem("openshelf-owner-token") ?? ""; }
  catch { return ""; }
}

export default function SourceSuggestions({ query }: { query: string }) {
  const { colors } = useTheme();
  const [books, setBooks] = useState<SourceBook[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [token, setToken] = useState(savedToken);
  const [tokenOpen, setTokenOpen] = useState(false);
  const [selected, setSelected] = useState<SourceBook | null>(null);
  const [job, setJob] = useState<GenerationJob | null>(null);
  const [busy, setBusy] = useState(false);
  const version = useRef(0);

  useEffect(() => {
    const current = ++version.current;
    if (query.trim().length < 2) { setBooks([]); setLoading(false); return; }
    setLoading(true);
    const timer = setTimeout(() => {
      fetchSourceBooks(query.trim()).then((data) => {
        if (current === version.current) { setBooks(data.books); setError(""); }
      }).catch((reason) => {
        if (current === version.current) { setBooks([]); setError(reason instanceof Error ? reason.message : "Search unavailable"); }
      }).finally(() => { if (current === version.current) setLoading(false); });
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!job || !token || (job.state !== "queued" && job.state !== "running")) return;
    const interval = setInterval(() => {
      fetchGenerationJob(job.id, token).then(setJob).catch(() => setError("Could not refresh job status"));
    }, 5000);
    return () => clearInterval(interval);
  }, [job?.id, job?.state, token]);

  const act = async (book: SourceBook) => {
    setSelected(book);
    setError("");
    if (!token.trim()) { setTokenOpen(true); return; }
    setBusy(true);
    try {
      const next = book.state === "failed" && book.job_id
        ? await retryGenerationJob(book.job_id, token.trim())
        : book.job_id && (book.state === "queued" || book.state === "running")
          ? await fetchGenerationJob(book.job_id, token.trim())
          : await createGenerationJob(book.source_id, token.trim());
      setJob(next);
      setTokenOpen(false);
      try { if (typeof sessionStorage !== "undefined") sessionStorage.setItem("openshelf-owner-token", token.trim()); } catch { /* native memory only */ }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not create job");
    } finally { setBusy(false); }
  };

  if (query.trim().length < 2) return null;
  return <View style={{ paddingHorizontal: 16, paddingBottom: 12 }}>
    <Text style={{ color: colors.text, fontSize: 18, fontWeight: "600", marginBottom: 5 }}>Find a Gutenberg edition</Text>
    <Text style={{ color: colors.textSecondary, marginBottom: 5 }}>This early source index is limited. Only the owner can schedule audio generation.</Text>
    {loading && <ActivityIndicator accessibilityLabel="Searching source books" color={colors.primary} />}
    {error ? <Text accessibilityRole="alert" style={{ color: colors.textSecondary, marginVertical: 8 }}>{error}</Text> : null}
    {!loading && !error && books.length === 0 && <Text style={{ color: colors.textSecondary, marginVertical: 8 }}>No indexed edition found. Try another title or author.</Text>}
    {books.map((book) => <View key={book.source_id} style={{ paddingVertical: 9, borderBottomWidth: 0.5, borderColor: colors.separator }}>
      <Text style={{ color: colors.text, fontWeight: "600" }}>{book.title}</Text>
      <Text style={{ color: colors.textSecondary }}>{book.author} · Project Gutenberg #{book.source_id.split(":")[1]}</Text>
      {book.state === "available" && book.author_slug && book.title_slug
        ? <Link href={`/book/${book.author_slug}/${book.title_slug}`} asChild><Pressable accessibilityRole="button"><Text style={{ color: colors.primary }}>Open audiobook</Text></Pressable></Link>
        : <Pressable accessibilityRole="button" disabled={busy} onPress={() => act(book)}>
          <Text style={{ color: colors.primary }}>{book.state === "failed" ? "Retry generation" : book.state === "queued" || book.state === "running" ? "View generation" : "Generate audio"}</Text>
        </Pressable>}
    </View>)}
    {tokenOpen && selected && <View style={{ marginTop: 12 }}>
      <Text style={{ color: colors.text }}>OpenShelf owner key for {selected.title}</Text>
      <Text style={{ color: colors.textSecondary }}>Use the dedicated OpenShelf key, not a Cloudflare API token. It is kept only for this session.</Text>
      <TextInput accessibilityLabel="Owner token" secureTextEntry value={token} onChangeText={setToken}
        placeholder="Enter owner token" placeholderTextColor={colors.textSecondary}
        style={{ color: colors.text, borderColor: colors.separator, borderWidth: 1, padding: 8, marginVertical: 6 }} />
      <Pressable accessibilityRole="button" onPress={() => act(selected)}><Text style={{ color: colors.primary }}>Submit generation job</Text></Pressable>
    </View>}
    {job && <View style={{ marginTop: 12 }}>
      <Text style={{ color: colors.text }}>Generation {job.state}{job.state === "running" ? ` · ${job.stage}` : ""}</Text>
      {job.state === "failed" && <Text style={{ color: colors.textSecondary }}>
        {job.error_code === "BookTooLong"
          ? "This edition exceeds the PC's word budget. The owner can raise --max-words before retrying."
          : `Failed: ${job.error_code ?? "Unknown error"}. Search again to retry.`}
      </Text>}
      {job.state === "completed" && job.author_slug && job.title_slug &&
        <Link href={`/book/${job.author_slug}/${job.title_slug}`} asChild><Pressable accessibilityRole="button"><Text style={{ color: colors.primary }}>Open finished audiobook</Text></Pressable></Link>}
    </View>}
  </View>;
}
