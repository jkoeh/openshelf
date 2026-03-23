import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, FlatList, Pressable, Text, View } from "react-native";
import BookCard from "../components/BookCard";
import Header from "../components/Header";
import SearchBar from "../components/SearchBar";
import { useTheme } from "../hooks/useTheme";
import { fetchCatalog } from "../lib/api";
import type { CatalogBook } from "../types";

const PAGE_SIZE = 20;

export default function CatalogPage() {
  const { colors } = useTheme();
  const [books, setBooks] = useState<CatalogBook[]>([]);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadBooks = useCallback(
    async (pageNum: number, search: string, append: boolean) => {
      try {
        setLoading(true);
        setError(null);
        const data = await fetchCatalog({
          q: search || undefined,
          page: pageNum,
          limit: PAGE_SIZE,
        });
        const newBooks = data.books ?? [];
        setBooks((prev) => (append ? [...prev, ...newBooks] : newBooks));
        setHasMore(newBooks.length === PAGE_SIZE);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load catalog");
      } finally {
        setLoading(false);
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

  return (
    <View style={{ flex: 1, backgroundColor: colors.background }}>
      <Header />
      <SearchBar value={query} onChangeText={setQuery} />
      {error ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: 24 }}>
          <Text style={{ color: colors.textSecondary, fontSize: 16, marginBottom: 12 }}>
            {error}
          </Text>
          <Pressable onPress={() => loadBooks(1, query, false)}>
            <Text style={{ color: colors.primary }}>Retry</Text>
          </Pressable>
        </View>
      ) : (
        <FlatList
          data={books}
          keyExtractor={(item) => `${item.author_slug}/${item.title_slug}`}
          renderItem={({ item }) => <BookCard book={item} />}
          contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 24 }}
          onEndReached={loadMore}
          onEndReachedThreshold={0.5}
          ListEmptyComponent={
            !loading ? (
              <View style={{ alignItems: "center", paddingTop: 48 }}>
                <Text style={{ color: colors.textSecondary, fontSize: 16 }}>
                  {query ? "No books found" : "No books available"}
                </Text>
              </View>
            ) : null
          }
          ListFooterComponent={
            loading ? (
              <ActivityIndicator
                style={{ paddingVertical: 20 }}
                color={colors.primary}
              />
            ) : null
          }
        />
      )}
    </View>
  );
}
