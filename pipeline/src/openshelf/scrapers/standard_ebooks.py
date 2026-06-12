"""Standard Ebooks book search via HTML catalog scraping."""

import re
import urllib.parse

from openshelf.scrapers.http import make_request, matches_filter


def _iter_downloadable_ebook_paths(text):
    """Yield Standard Ebooks detail paths that are available for download."""
    saw_schema_entries = False
    for match in re.finditer(r"<li\b(?P<attrs>[^>]*)>", text, flags=re.IGNORECASE):
        attrs = match.group("attrs")
        about_match = re.search(
            r'\babout=(["\'])(?P<path>/ebooks/[a-z0-9][a-z0-9/_-]*)\1',
            attrs,
        )
        if not about_match:
            continue

        saw_schema_entries = True
        class_match = re.search(r'\bclass=(["\'])(?P<class>.*?)\1', attrs)
        classes = set((class_match.group("class") if class_match else "").split())
        if "not-pd" in classes:
            continue

        yield about_match.group("path")

    if saw_schema_entries:
        return

    yield from re.findall(
        r'href="(/ebooks/[a-z][a-z0-9-]*/[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*)*)"',
        text,
    )


def se_search(author=None, language=None, delay=2):
    """Yield (author_name, title, epub_url) from Standard Ebooks.

    Scrapes the public website listing pages. The OPDS feeds require
    authentication (patron access), so we use the HTML catalog instead.
    """
    base_url = "https://standardebooks.org/ebooks"
    params = {}
    if author:
        params["query"] = author
    page_num = 1

    while True:
        if page_num > 1:
            params["page"] = str(page_num)
        url = base_url
        if params:
            url += "?" + urllib.parse.urlencode(params)

        print(f"  Standard Ebooks: fetching page {page_num} ({url}) …")
        data = make_request(url, delay=delay)
        if data is None:
            break

        text = data.decode("utf-8", errors="replace")

        if "No ebooks matched" in text:
            if page_num == 1:
                print("  Standard Ebooks: no results found.")
            break

        # Find ebook page links from schema:Book list entries. Search results
        # also include not-yet-public-domain placeholders, which do not have
        # downloadable EPUB files.
        ebook_paths = list(_iter_downloadable_ebook_paths(text))

        if not ebook_paths:
            break

        seen = set()
        found_any = False
        for path in ebook_paths:
            parts = path.strip("/").split("/")
            if len(parts) < 3 or parts[0] != "ebooks":
                continue
            if path in seen:
                continue
            seen.add(path)
            found_any = True

            author_slug = parts[1]
            title_slug = parts[2]

            # Build download filename: author_title_translator.epub
            # Path parts use _ between components (e.g. willa-muir_edwin-muir)
            # but the filename joins ALL parts with _ after replacing internal _
            # separators with - (e.g. willa-muir-edwin-muir)
            filename = "_".join(p.replace("_", "-") for p in parts[1:]) + ".epub"
            epub_url = f"https://standardebooks.org{path}/downloads/{filename}"

            author_name = author_slug.replace("-", " ").title()
            title_name = title_slug.replace("-", " ").title()

            if not matches_filter(author, author_name):
                continue

            yield author_name, title_name, epub_url

        if not found_any or f'page={page_num + 1}' not in text:
            break
        page_num += 1
