"""Project Gutenberg book search via the Gutendex API."""

import json
import urllib.parse

from openshelf.config import GUTENDEX_API
from openshelf.scrapers.http import make_request


def gutenberg_search(author=None, language=None, subject=None, delay=2):
    """Yield (author_name, title, epub_url) from the Gutendex API."""
    params = {}
    if author:
        params["search"] = author
    if language:
        params["languages"] = language
    if subject:
        params["topic"] = subject

    url = GUTENDEX_API
    if params:
        url += "?" + urllib.parse.urlencode(params)

    page = 1
    while url:
        print(f"  Gutenberg: fetching page {page} …")
        data = make_request(url, delay=delay)
        if data is None:
            break

        body = json.loads(data)
        for book in body.get("results", []):
            epub_url = book.get("formats", {}).get("application/epub+zip")
            if not epub_url:
                continue
            authors = book.get("authors", [])
            author_name = authors[0]["name"] if authors else "unknown"
            title = book.get("title", "untitled")
            yield author_name, title, epub_url

        url = body.get("next")
        page += 1
