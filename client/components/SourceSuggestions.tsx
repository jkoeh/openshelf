import { useRouter } from "expo-router";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, Text, useWindowDimensions, View } from "react-native";
import { discoveryColors } from "../constants/discovery";
import { useTheme } from "../hooks/useTheme";
import { ApiError, cancelGenerationJob, fetchGenerationJob, fetchSourceBooks, regenerateGenerationJob, requestExpressiveGenerationJob, retryGenerationJob } from "../lib/api";
import { latestJob } from "../lib/generation-jobs";
import type { GenerationJob, SourceBook } from "../types";

function failureText(code: GenerationJob["error_code"] | undefined) {
  if (code === "RIGHTS_NOT_VERIFIED") return "Rights could not be verified for this edition. The owner can review it.";
  if (code === "BOOK_TOO_LONG") return "Audio generation stopped under an earlier word limit. That limit has been removed; the owner can retry. You can still read now.";
  return "Generation stopped. The owner can review the job.";
}

export default function SourceSuggestions({ query, adminToken, onAdminExpired }: {
  query: string;
  adminToken: string | null;
  onAdminExpired: () => void;
}) {
  const router = useRouter();
  const { theme, colors } = useTheme();
  const palette = discoveryColors(theme, colors);
  const { width } = useWindowDimensions();
  const [books, setBooks] = useState<SourceBook[]>([]);
  const [jobs, setJobs] = useState<Record<string, GenerationJob>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [busySource, setBusySource] = useState<string | null>(null);
  const [pendingExpressiveSource, setPendingExpressiveSource] = useState<string | null>(null);
  const version = useRef(0);

  useEffect(() => {
    const current = ++version.current;
    if (query.trim().length < 2) {
      setBooks([]);
      setLoading(false);
      setError("");
      return;
    }
    setBooks([]);
    setLoading(true);
    const timer = setTimeout(() => {
      fetchSourceBooks(query.trim()).then((data) => {
        if (current === version.current) { setBooks(data.books); setError(""); }
      }).catch((reason) => {
        if (current === version.current) {
          setBooks([]);
          setError(reason instanceof Error ? reason.message : "Search is unavailable. Try again soon.");
        }
      }).finally(() => { if (current === version.current) setLoading(false); });
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  const activeIds = books.flatMap((book) => {
    const current = latestJob(book, jobs[book.source_id]);
    const state = current?.state ?? book.job_state ?? book.state;
    const id = current?.id ?? book.job_id;
    return id && (state === "queued" || state === "running") ? [id] : [];
  }).join(",");

  useEffect(() => {
    if (!activeIds) return;
    let alive = true;
    const refresh = () => {
      for (const id of activeIds.split(",")) {
        fetchGenerationJob(id).then((job) => {
          if (alive) setJobs((previous) => ({ ...previous, [job.source_id]: job }));
        }).catch(() => {
          if (alive) setError("Could not refresh generation status. It will retry shortly.");
        });
      }
    };
    refresh();
    const interval = setInterval(refresh, 15_000);
    return () => { alive = false; clearInterval(interval); };
  }, [activeIds]);

  const adminAct = async (book: SourceBook, action: "cancel" | "retry" | "regenerate") => {
    if (!adminToken) return;
    const id = latestJob(book, jobs[book.source_id])?.id ?? book.job_id;
    setBusySource(book.source_id);
    setError("");
    try {
      const next = action === "regenerate"
        ? await regenerateGenerationJob(book.source_id, adminToken)
        : action === "cancel" && id
          ? await cancelGenerationJob(id, adminToken)
          : id ? await retryGenerationJob(id, adminToken) : null;
      if (next) setJobs((previous) => ({ ...previous, [book.source_id]: next }));
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onAdminExpired();
      setError(reason instanceof Error ? reason.message : "Owner action failed.");
    } finally {
      setBusySource(null);
    }
  };

  const expressiveAct = async (book: SourceBook) => {
    if (!adminToken) return;
    setBusySource(book.source_id);
    setError("");
    try {
      const current = latestJob(book, jobs[book.source_id]);
      const published = book.state === "available" || current?.state === "completed";
      const next = await requestExpressiveGenerationJob(
        book.source_id, adminToken, published,
      );
      setJobs((previous) => ({ ...previous, [book.source_id]: next }));
      setPendingExpressiveSource(null);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onAdminExpired();
      setError(reason instanceof Error ? reason.message : "Could not request expressive audio.");
    } finally {
      setBusySource(null);
    }
  };

  if (query.trim().length < 2) return null;

  return <View style={{ marginTop: 34 }}>
    <Text accessibilityRole="header" style={{ color: palette.text, fontFamily: "Georgia", fontSize: width < 500 ? 25 : 32, fontWeight: "700" }}>
      Project Gutenberg editions
    </Text>
    <Text style={{ color: palette.muted, fontSize: 15, lineHeight: 22, marginTop: 5, marginBottom: 18 }}>
      Read immediately. We will prepare the audio while you turn the pages. Search includes indexed editions only.
    </Text>
    {loading && <ActivityIndicator accessibilityLabel="Searching source books" color={palette.primary} style={{ marginVertical: 24 }} />}
    {error ? <Text accessibilityRole="alert" style={{ color: palette.text, marginBottom: 12 }}>{error}</Text> : null}
    {!loading && !error && books.length === 0 && (
      <Text style={{ color: palette.muted, marginVertical: 12 }}>No indexed edition found. Try another title or author.</Text>
    )}
    <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 16 }}>
      {books.map((book) => {
        const job = latestJob(book, jobs[book.source_id]);
        const state = job?.state ?? book.job_state ?? book.state;
        const authorSlug = job?.author_slug ?? book.author_slug;
        const titleSlug = job?.title_slug ?? book.title_slug;
        const active = state === "queued" || state === "running";
        const mode = job?.mode ?? book.job_mode ?? "standard";
        const ready = book.state === "available" || state === "completed";
        const failed = state === "failed";
        return <View key={book.source_id} style={{
          width: width >= 760 ? "48%" : "100%",
          flexGrow: width >= 760 ? 1 : 0,
          backgroundColor: palette.card, borderColor: palette.border, borderWidth: 1,
          borderRadius: 16, padding: width < 760 ? 18 : 20,
        }}>
          <Text style={{ color: palette.text, fontFamily: "Georgia", fontSize: width < 760 ? 21 : 23, lineHeight: 27, fontWeight: "700" }}>
            {book.title}
          </Text>
          <Text style={{ color: palette.text, fontSize: 15, marginTop: 6 }}>{book.author}</Text>
          <Text style={{ color: palette.muted, fontSize: 13, marginTop: 8, marginBottom: 14 }}>
            Gutenberg #{book.source_id.split(":")[1]}
          </Text>
          {active && <View style={{ marginBottom: 16 }}>
            <Text style={{ color: palette.text, fontWeight: "600" }}>
              {mode === "expressive" ? "Expressive" : "Standard"} generation {state}{state === "running" && job?.stage ? ` · ${job.stage}` : ""}
            </Text>
            <Text selectable style={{ color: palette.muted, fontSize: 12, marginTop: 4 }}>Job ID: {job?.id ?? book.job_id}</Text>
          </View>}
          {failed && <Text style={{ color: palette.muted, lineHeight: 20, marginBottom: 16 }}>
            {book.state === "available" ? "Latest regeneration stopped. The existing audiobook is still available." : failureText(job?.error_code ?? book.job_error_code)}
          </Text>}
          {state === "canceled" && <Text style={{ color: palette.muted, marginBottom: 16 }}>Request canceled.</Text>}
          {job?.state === "completed" && <Text style={{ color: palette.text, fontWeight: "600", marginBottom: 16 }}>Generation completed</Text>}
          <Pressable accessibilityRole="button" onPress={() => {
            if (ready && authorSlug && titleSlug) {
              router.push(`/read/${authorSlug}/${titleSlug}`);
            } else {
              router.push({ pathname: "/source/[id]", params: {
                id: book.source_id, title: book.title, author: book.author,
                job: job?.id ?? book.job_id ?? "", state,
              } });
            }
          }} style={{ backgroundColor: palette.primary, borderRadius: 9, minHeight: 48,
            alignItems: "center", justifyContent: "center", paddingHorizontal: 12 }}>
            <Text style={{ color: palette.primaryText, fontSize: 16, fontWeight: "600" }}>Read now</Text>
          </Pressable>
          {adminToken && ((active && (job?.id ?? book.job_id)) || (failed && (job?.id ?? book.job_id)) || ready) && (
            <Pressable accessibilityRole="button" disabled={!!busySource}
              onPress={() => adminAct(book, active ? "cancel" : failed ? "retry" : "regenerate")}
              style={{ minHeight: 44, justifyContent: "center", alignItems: "center", marginTop: 10,
                borderWidth: 1, borderColor: palette.border, borderRadius: 9, opacity: busySource ? 0.6 : 1 }}>
              <Text style={{ color: palette.primary, fontWeight: "600" }}>
                {active ? "Cancel generation" : failed ? "Retry generation" : "Regenerate audio"}
              </Text>
            </Pressable>
          )}
          {adminToken && !active && !(failed && mode === "expressive") && (
            pendingExpressiveSource === book.source_id ? <View style={{ marginTop: 12, padding: 14,
              borderWidth: 1, borderColor: palette.border, borderRadius: 9 }}>
              <Text style={{ color: palette.text, lineHeight: 21 }}>
                Expressive audio uses OpenAI emotion direction and Chatterbox on your PC.
                This spends one of the 300 daily generation starts.
              </Text>
              <View style={{ flexDirection: "row", gap: 12, marginTop: 12 }}>
                <Pressable accessibilityRole="button" disabled={!!busySource}
                  onPress={() => expressiveAct(book)} style={{ flex: 1, minHeight: 44,
                    backgroundColor: palette.primary, borderRadius: 9,
                    alignItems: "center", justifyContent: "center", paddingHorizontal: 8 }}>
                  <Text style={{ color: palette.primaryText, fontWeight: "600", textAlign: "center" }}>Start expressive job</Text>
                </Pressable>
                <Pressable accessibilityRole="button" onPress={() => setPendingExpressiveSource(null)}
                  style={{ minHeight: 44, paddingHorizontal: 10, justifyContent: "center" }}>
                  <Text style={{ color: palette.primary }}>Cancel</Text>
                </Pressable>
              </View>
            </View> : <Pressable accessibilityRole="button" disabled={!!busySource}
              onPress={() => setPendingExpressiveSource(book.source_id)}
              style={{ minHeight: 44, justifyContent: "center", alignItems: "center", marginTop: 10,
                borderWidth: 1, borderColor: palette.border, borderRadius: 9, opacity: busySource ? 0.6 : 1 }}>
              <Text style={{ color: palette.primary, fontWeight: "600" }}>Generate expressive audio</Text>
            </Pressable>
          )}
        </View>;
      })}
    </View>
  </View>;
}
