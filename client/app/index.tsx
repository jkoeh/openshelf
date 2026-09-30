import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, FlatList, Pressable, Text, useWindowDimensions, View } from "react-native";
import BookCard from "../components/BookCard";
import SearchBar from "../components/SearchBar";
import SourceSuggestions from "../components/SourceSuggestions";
import { discoveryColors } from "../constants/discovery";
import { useTheme } from "../hooks/useTheme";
import { fetchCatalog } from "../lib/api";
import type { CatalogBook } from "../types";

const PAGE_SIZE = 20;

export default function CatalogPage() {
  const { colors, theme } = useTheme();
  const palette = discoveryColors(theme, colors);
  const { width } = useWindowDimensions();
  const [books, setBooks] = useState<CatalogBook[]>([]);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const requestVersion = useRef(0);

  const loadBooks = useCallback(
    async (pageNum: number, search: string, append: boolean) => {
      const version = ++requestVersion.current;
      try {
        setLoading(true);
        setError(null);
        const data = await fetchCatalog({
          q: search || undefined,
          page: pageNum,
          limit: PAGE_SIZE,
        });
        if (version !== requestVersion.current) return;
        const newBooks = data.books ?? [];
        setBooks((prev) => (append ? [...prev, ...newBooks] : newBooks));
        setHasMore(newBooks.length === PAGE_SIZE);
      } catch (e) {
        if (version !== requestVersion.current) return;
        setError(e instanceof Error ? e.message : "Failed to load catalog");
      } finally {
        if (version === requestVersion.current) setLoading(false);
      }
    },
    [],
  );

  useEffect(() => {
    const timeout = setTimeout(() => {
      setPage(1);
      loadBooks(1, query, false);
    }, 300);
    return () => clearTimeout(timeout);
  }, [query, loadBooks]);

  const loadMore = () => {
    if (!loading && hasMore) {
      const next = page + 1;
      setPage(next);
      loadBooks(next, query, true);
    }
  };

  const contentWidth = Math.min(width - (width < 600 ? 36 : 72), 1040);

  return (
    <View style={{ flex: 1, backgroundColor: palette.background }}>
        <FlatList
          data={books}
          keyExtractor={(item) => `${item.author_slug}/${item.title_slug}`}
          renderItem={({ item }) => <BookCard book={item} />}
          contentContainerStyle={{ width: contentWidth, alignSelf: "center", paddingBottom: 64 }}
          ListHeaderComponent={<View style={{ paddingTop: width < 600 ? 32 : 54 }}>
            <Text style={{ color: palette.text, fontSize: 24, fontWeight: "700", letterSpacing: -1 }}>OpenShelf</Text>
            <Text accessibilityRole="header" style={{
              color: palette.text, fontFamily: "Georgia", fontWeight: "700",
              fontSize: width < 600 ? 34 : 58, lineHeight: width < 600 ? 42 : 66,
              letterSpacing: -1.6, marginTop: width < 600 ? 40 : 48,
            }}>Find your next listen</Text>
            <Text style={{ color: palette.muted, fontSize: width < 600 ? 16 : 20, lineHeight: 25, marginTop: 8, marginBottom: 25 }}>
              Discover public-domain books and request audio.
            </Text>
            <SearchBar value={query} onChangeText={setQuery} />
            <SourceSuggestions query={query} />
            {books.length > 0 && <Text accessibilityRole="header" style={{
              color: palette.text, fontFamily: "Georgia", fontSize: 27,
              fontWeight: "700", marginTop: 36, marginBottom: 8,
            }}>Ready to listen</Text>}
            {error && <View style={{ marginTop: 24 }}>
              <Text accessibilityRole="alert" style={{ color: palette.muted, marginBottom: 10 }}>Published books could not load: {error}</Text>
              <Pressable accessibilityRole="button" onPress={() => loadBooks(1, query, false)}><Text style={{ color: palette.primary }}>Retry published books</Text></Pressable>
            </View>}
          </View>}
          onEndReached={loadMore}
          onEndReachedThreshold={0.5}
          ListEmptyComponent={
            !loading && !error && query.trim().length < 2 ? (
              <View style={{ alignItems: "center", paddingTop: 48 }}>
                <Text style={{ color: palette.muted, fontSize: 16 }}>
                  No audiobooks published yet. Search for an edition to request one.
                </Text>
              </View>
            ) : null
          }
          ListFooterComponent={
            loading ? (
              <ActivityIndicator
                style={{ paddingVertical: 20 }}
                color={palette.primary}
              />
            ) : null
          }
        />
    </View>
  );
}
