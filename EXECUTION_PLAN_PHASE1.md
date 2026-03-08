# Execution Plan: EPUB Parser + Text Chunker (Steps 1 & 2)

## Context

Implement the first two pipeline steps from PLAN.md with a **test-first** approach. Both modules are currently stubs. The `Chapter` dataclass already exists in `epub_parser.py`.

## Implementation Order

1. Write `tests/pipeline/test_text_chunker.py` (tests first)
2. Implement `src/openshelf/pipeline/text_chunker.py`
3. Write `tests/pipeline/test_epub_parser.py` (tests first)
4. Implement `src/openshelf/pipeline/epub_parser.py`

---

## Step 2: text_chunker.py (implement first -- pure function, no deps)

### Function Signature
```python
from openshelf.config import CHUNK_MAX_WORDS

def chunk_text(text: str, max_words: int = CHUNK_MAX_WORDS) -> list[str]:
```

### Logic (paragraph-aware)
1. Empty/whitespace -> return `[]`
2. Split on paragraph boundaries (`\n\n`) first
3. For each paragraph:
   a. Protect abbreviations by replacing their periods with a placeholder
   b. Split on sentence boundaries: `(?<=[.!?])\s+`
   c. Restore abbreviation periods
4. Greedily pack paragraphs into chunks up to `max_words`
   - Short paragraphs can share a chunk (joined by `" "`)
   - A chunk never internally contains `\n\n`
5. Oversized paragraph -> split at sentence boundaries within paragraph
6. Oversized single sentence -> split at comma boundaries (`, `)
7. Oversized comma fragment -> split at word boundaries

### Private Helpers
- `_split_sentences(text: str) -> list[str]` -- abbreviation-aware sentence splitting
- `_chunk_paragraph(paragraph: str, max_words: int) -> list[str]` -- split oversized paragraph at sentences
- `_split_at_commas(sentence: str, max_words: int) -> list[str]`
- `_split_at_words(fragment: str, max_words: int) -> list[str]`

### Abbreviation Set
```python
_ABBREVIATIONS = {
    "Mr", "Mrs", "Ms", "Dr", "St", "Jr", "Sr", "Prof", "Gen", "Gov",
    "Sgt", "Cpl", "Pvt", "Corp", "Inc", "Ltd", "vs", "etc", "al", "approx",
    "dept", "est", "vol", "No", "Rev", "Hon", "Pres", "Vol", "Dept", "Univ",
    "Co", "Mt", "Ft", "Capt", "Lt", "Maj", "Col",
}
```
Also protect single uppercase letters (initials like J. K. Rowling).

### Test Cases (`tests/pipeline/test_text_chunker.py`)

No mocks needed -- pure function. Use helper `_words(n)` that returns a string of n words.

**TestChunkTextBasic:**
- `test_empty_string` -> `[]`
- `test_whitespace_only` -> `[]`
- `test_single_word` -> `["Hello"]`
- `test_short_text_single_chunk` -- 100 words stays as 1 chunk
- `test_text_exactly_at_max` -- 450 words, 1 chunk

**TestChunkTextSentenceSplitting:**
- `test_splits_on_period` -- two 300-word sentences -> 2 chunks
- `test_splits_on_exclamation`
- `test_splits_on_question_mark`
- `test_no_mid_sentence_split` -- 400w + 100w sentences stay intact
- `test_greedy_accumulation` -- 200w + 200w + 200w -> [400w, 200w]

**TestChunkTextPunctuationEdgeCases:**
- `test_ellipsis` -- "He paused... Then continued." no empty chunks
- `test_multiple_punctuation` -- "Really?! You think so?!"
- `test_text_ending_without_punctuation`
- `test_quoted_dialogue`
- `test_newlines_between_paragraphs`

**TestChunkTextAbbreviations:**
- `test_dr_not_split` -- "Dr. Smith" stays together
- `test_mr_mrs_not_split`
- `test_etc_not_split`
- `test_single_letter_initial` -- "J. K. Rowling"

**TestChunkTextOversizedSentences:**
- `test_over_max_splits_at_commas`
- `test_no_commas_splits_at_words`
- `test_mix_normal_and_oversized`
- `test_sentence_exactly_at_max_no_split`
- `test_sentence_at_451_words_no_commas` -> [450, 1]

**TestChunkTextParagraphAwareness:**
- `test_paragraphs_never_cross_boundary` -- no chunk contains `\n\n`
- `test_short_paragraphs_packed` -- two 100-word paragraphs -> 1 chunk
- `test_oversized_paragraph_split_at_sentences` -- 600-word paragraph -> multiple chunks
- `test_paragraph_boundary_respected` -- 300w para + 300w para -> 2 chunks (not merged)
- `test_dialogue_paragraphs_packed` -- many short dialogue paragraphs packed together

**TestChunkTextInvariants:**
- `test_no_chunk_exceeds_max` -- large text, assert all chunks <= 450 words
- `test_all_words_preserved` -- no words lost
- `test_order_preserved`

---

## Step 1: epub_parser.py

### Function Signature
```python
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup

def parse_epub(epub_path: str) -> list[Chapter]:
```

### Constants
```python
_SKIP_PATTERNS = ("nav", "toc", "cover")  # titlepage kept so it's read aloud as the audiobook opening
_MIN_WORD_COUNT = 50
```

### Logic
1. `epub.read_epub(epub_path)` -> Book
2. Iterate `book.get_items_of_type(ebooklib.ITEM_DOCUMENT)`
3. Skip if filename (from `item.get_name()`) contains any of `_SKIP_PATTERNS` (case-insensitive). Titlepage is NOT skipped -- it becomes the opening audio ("Crime and Punishment by Fyodor Dostoevsky").
4. Parse HTML with `BeautifulSoup(item.get_content(), "html.parser")`
5. Remove `<sup>`, `<sub>`, and `<a>` tags with numeric-only content via `.decompose()`
6. Title: first `h1` -> `h2` -> `h3` via `soup.find()`, use `.get_text(strip=True)`. Fallback `"Chapter N"`
7. Text: find all `<p>` tags, normalize whitespace within each, join with `"\n\n"`
8. If no `<p>` tags yield text, fall back to `soup.get_text()` with whitespace normalization
9. Skip if `len(text.split()) < _MIN_WORD_COUNT`
10. Append `Chapter(number=n, title=title, text=text, word_count=len(text.split()))`

### Test Cases (`tests/pipeline/test_epub_parser.py`)

Mock strategy: mock `ebooklib.epub.read_epub`. Helpers:
- `_make_item(filename, html)` -- returns MagicMock with `get_name()` and `get_content()`
- `_make_book(items)` -- returns MagicMock whose `get_items_of_type()` returns items

**TestParseEpubBasic:**
- `test_single_chapter` -- one item with h2 + 50+ words
- `test_multiple_chapters_numbered` -- 3 items -> numbers 1,2,3
- `test_word_count_correct`
- `test_paragraph_separation` -- `<p>A.</p><p>B.</p>` -> `"A.\n\nB."`
- `test_whitespace_normalization` -- `"  lots   of   spaces  "` -> single spaces

**TestParseEpubTitleExtraction:**
- `test_title_from_h1`
- `test_title_from_h2_when_no_h1`
- `test_title_from_h3_fallback`
- `test_fallback_chapter_n` -- no headings -> "Chapter 1"
- `test_h1_preferred_over_h2`

**TestParseEpubFiltering:**
- `test_skips_nav`, `test_skips_toc`, `test_skips_cover`
- `test_titlepage_not_skipped` -- titlepage is kept as audiobook opening
- `test_skip_is_case_insensitive_substring` -- "EPUB/Navigation.xhtml" skipped
- `test_skips_under_50_words`
- `test_keeps_exactly_50_words`
- `test_numbering_skips_filtered` -- [nav, ch-a, short, ch-b] -> Chapter 1, Chapter 2

**TestParseEpubHtmlCleaning:**
- `test_sup_tags_removed` -- `<sup>1</sup>` content gone
- `test_sub_tags_removed`
- `test_numeric_anchor_tags_removed` -- `<a href="#note1">1</a>` content gone
- `test_non_numeric_anchor_tags_kept` -- `<a href="...">click here</a>` text preserved
- `test_nested_html_preserved` -- `<em>`, `<strong>` text kept
- `test_malformed_html_no_crash`

**TestParseEpubEdgeCases:**
- `test_empty_epub` -> `[]`
- `test_whitespace_only_item` -- skipped
- `test_unicode_content` -- em-dashes, curly quotes preserved
- `test_no_p_tags` -- falls back to soup.get_text()
- `test_image_only_item` -- skipped

---

## Files to Modify

| File | Action |
|------|--------|
| `tests/pipeline/test_text_chunker.py` | Create (tests first) |
| `src/openshelf/pipeline/text_chunker.py` | Implement |
| `tests/pipeline/test_epub_parser.py` | Create (tests first) |
| `src/openshelf/pipeline/epub_parser.py` | Extend (add `parse_epub`, keep `Chapter`) |

## Conventions (from existing codebase)

- Tests use `unittest.TestCase` + `unittest.mock`, no pytest
- `sys.path.insert(0, ...)` at top of test files for import without pip install
- Constants from `config.py`, never hardcoded
- All tests fully mocked -- no real I/O
- Follow patterns in `tests/scrapers/test_download_books.py`

## Verification

```bash
python3 -m unittest tests.pipeline.test_text_chunker -v
python3 -m unittest tests.pipeline.test_epub_parser -v
python3 -m unittest discover -s tests -v  # all tests still pass
```

## Design Notes

- **Abbreviation handling**: Fixed list is imperfect but avoids NLP deps. Acceptable for TTS where a mis-split only causes a small audio pause.
- **`<p>` tag extraction**: Primary strategy. Fallback to `soup.get_text()` handles EPUBs using `<div>` for paragraphs.
- **Skip filter**: Substring match on lowercase filename for `nav`, `toc`, `cover`. Titlepage is intentionally kept so the audiobook opens with "Title by Author".
- **`<sup>`/`<sub>` removal**: Uses `decompose()` to remove tag AND content. Correct for TTS.
- **`<a>` tag stripping**: Numeric-only anchors (e.g., `[1]`) are common Gutenberg footnote links. Decomposed to avoid TTS reading "one" mid-sentence. Non-numeric anchors kept.
- **Paragraph-aware chunking**: Chunks never cross `\n\n` boundaries. Short paragraphs are packed together. This preserves prosody boundaries for TTS.
- **Extended abbreviation set**: Includes military/geographic abbreviations (Mt., Ft., Capt., Lt., Maj., Col.) common in 19th-century literature.
