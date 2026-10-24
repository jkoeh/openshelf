"""Exact generated-edition access through Project Gutenberg's listed mirror."""

import re
from urllib.parse import urlsplit

MIRROR = "https://gutenberg.pglaf.org"
LEGACY_HOSTS = {"www.gutenberg.org", "dev.gutenberg.org", "gutenberg.org"}


def source_number(source_id: str) -> str:
    match = re.fullmatch(r"gutenberg:([1-9][0-9]*)", source_id)
    if not match:
        raise ValueError("invalid Gutenberg ID")
    return match.group(1)


def mirror_url(url: str, source_id: str) -> str:
    number = source_number(source_id)
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname not in LEGACY_HOSTS | {"gutenberg.pglaf.org"}
            or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment):
        raise ValueError("untrusted Gutenberg URL")
    match = re.fullmatch(rf"/ebooks/{number}\.epub(3)?(?:\.(images|noimages))?", parsed.path)
    if match:
        variant = "-images" if match.group(2) == "images" else ""
        version = "-3" if match.group(1) else ""
        path = f"/cache/epub/{number}/pg{number}{variant}{version}.epub"
    elif re.fullmatch(rf"/cache/epub/{number}/pg{number}(?:-images)?(?:-3)?\.epub", parsed.path):
        path = parsed.path
    else:
        raise ValueError("Gutenberg EPUB does not match source ID")
    return MIRROR + path


def rights_url(source_id: str) -> str:
    number = source_number(source_id)
    return f"{MIRROR}/cache/epub/{number}/pg{number}.rdf"
