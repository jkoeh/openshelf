"""Standard Ebooks book search via HTML catalog scraping."""

import re
import urllib.parse

from openshelf.scrapers.http import make_request


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

        # Find ebook page links from about= attributes on schema:Book elements
        ebook_paths = re.findall(
            r'about="(/ebooks/[a-z0-9][a-z0-9/_-]*)"', text
        )
        if not ebook_paths:
            ebook_paths = re.findall(
                r'href="(/ebooks/[a-z][a-z0-9-]*/[a-z][a-z0-9-]*(?:/[a-z][a-z0-9-]*)*)"',
                text,
            )

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

            filename_parts = parts[1:]
            filename = "_".join(filename_parts) + ".epub"
            epub_url = f"https://standardebooks.org{path}/downloads/{filename}"

            author_name = author_slug.replace("-", " ").title()
            title_name = title_slug.replace("-", " ").title()

            if author and author.lower() not in author_name.lower():
                continue

            yield author_name, title_name, epub_url

        if not found_any or f'page={page_num + 1}' not in text:
            break
        page_num += 1
