"""Official Gutenberg catalog import stays bounded and never claims a job."""

import csv
import gzip
import io
import tarfile
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from openshelf.pipeline import source_catalog
from openshelf.pipeline import standard_ebooks_source as se


class CatalogTests(unittest.TestCase):
    def test_standard_ebooks_metadata_rights_and_bounded_import(self):
        source_id = "standardebooks:lewis-carroll/alices-adventures-in-wonderland/john-tenniel"
        path = "/ebooks/" + se.edition_path(source_id)
        page = (f'<link rel="canonical" href="{se.BASE}{path}"/>'
                '<h1 property="schema:name">Alice’s Adventures in Wonderland</h1>'
                '<a property="schema:author"><span property="schema:name">Lewis Carroll</span></a>'
                '<p class="us-pd-warning">This ebook is thought to be free of copyright restrictions in the United States.</p>'
                f'<a href="{se.epub_url(source_id).split(se.BASE)[1].split("?")[0]}">EPUB</a>')
        book = se.parse_edition(page, source_id)
        self.assertEqual(book["title"], "Alice’s Adventures in Wonderland")
        for invalid in (page.replace("us-pd-warning", "footer"),
                        page.replace("rel=\"canonical\"", "rel=\"other\""),
                        page.replace(".epub", ".azw3")):
            with self.assertRaises(ValueError):
                se.parse_edition(invalid, source_id)
        listing = f'<li about="{path}"></li><li class="not-pd" about="/ebooks/a/future"></li>'
        with patch.object(se, "fetch_page", side_effect=[listing, page]) as fetch:
            self.assertEqual(list(se.catalog_books(max_books=1)), [book])
            self.assertEqual(fetch.call_count, 2)
        posts = []
        with patch.object(se, "catalog_books", return_value=[book]), \
                patch.object(source_catalog.JobAPI, "__init__", return_value=None), \
                patch.object(source_catalog.JobAPI, "post", side_effect=lambda route, body: posts.append((route, body))), \
                patch.object(source_catalog.JobAPI, "source_epub_cached", return_value=False), \
                patch("openshelf.pipeline.job_consumer.fetch_epub", side_effect=lambda url, sid, path: path.write_bytes(b"PK\x03\x04")), \
                patch("openshelf.pipeline.job_consumer.check_epub_rights"), \
                patch("openshelf.pipeline.job_consumer.check_spoken_text", return_value=100), \
                patch.object(source_catalog.JobAPI, "upload_source_epub") as upload:
            self.assertEqual(source_catalog.main(["--source", "standard-ebooks", "--api-base", "https://example.com/api/v1"]), 0)
            self.assertEqual(upload.call_args.args[0], source_id)
        self.assertEqual(posts[0][0], "/internal/source-books/sync")
        self.assertEqual(posts[0][1]["books"], [book])

    def test_standard_ebooks_skips_cached_downloads_and_stops_on_rate_limit(self):
        books = [{"source_id": "standardebooks:author/one", "epub_url": "https://standardebooks.org/one"},
                 {"source_id": "standardebooks:author/two", "epub_url": "https://standardebooks.org/two"},
                 {"source_id": "standardebooks:author/three", "epub_url": "https://standardebooks.org/three"}]
        with patch.object(se, "catalog_books", return_value=books), \
                patch.object(source_catalog.JobAPI, "__init__", return_value=None), \
                patch.object(source_catalog.JobAPI, "source_epub_cached", side_effect=[True, False]) as cached, \
                patch("openshelf.pipeline.job_consumer.fetch_epub", side_effect=urllib.error.HTTPError(books[1]["epub_url"], 429, "Limited", {}, None)) as fetch, \
                patch.object(source_catalog.JobAPI, "post") as post:
            self.assertEqual(source_catalog.main(["--source", "standard-ebooks", "--api-base", "https://example.com/api/v1"]), 1)
            self.assertEqual(cached.call_count, 2)
            self.assertEqual(fetch.call_count, 1)
            post.assert_not_called()

    @staticmethod
    def rdf(number, rights, epub_url=None):
        file = (f'<pgterms:file rdf:about="{epub_url}"><dcterms:format>'
                f'<rdf:Description><rdf:value>application/epub+zip</rdf:value>'
                f'</rdf:Description></dcterms:format></pgterms:file>') if epub_url else ""
        return (f'<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
                f'xmlns:dcterms="http://purl.org/dc/terms/" '
                f'xmlns:pgterms="http://www.gutenberg.org/2009/pgterms/">'
                f'<pgterms:ebook rdf:about="ebooks/{number}">'
                f'<dcterms:rights>{rights}</dcterms:rights></pgterms:ebook>{file}</rdf:RDF>').encode()

    def test_filters_metadata_and_constructs_trusted_epub_url(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog = Path(folder) / "books.csv.gz"
            with gzip.open(catalog, "wt", encoding="utf-8", newline="") as output:
                writer = csv.writer(output)
                writer.writerow(["Text#", "Type", "Title", "Language", "Authors"])
                writer.writerow(["11", "Text", "Alice\n Adventures", "en", "Carroll, Lewis"])
                writer.writerow(["12", "Sound", "Audio only", "en", "Nobody"])
                writer.writerow(["13", "Text", "French", "fr", "Nobody"])
                writer.writerow(["14", "Text", "Later", "en", "Author"])
            rows = list(source_catalog.catalog_books(catalog, max_books=1))
            self.assertEqual(rows, [{"source_id": "gutenberg:11", "title": "Alice Adventures",
                                     "author": "Carroll, Lewis"}])
            self.assertEqual([row["source_id"] for row in source_catalog.catalog_books(catalog, after_id=11)],
                             ["gutenberg:14"])

    def test_batches_at_fifty_without_claiming_a_job(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog = Path(folder) / "books.csv"
            with catalog.open("w", encoding="utf-8", newline="") as output:
                writer = csv.writer(output)
                writer.writerow(["Text#", "Type", "Title", "Language", "Authors"])
                for number in range(1, 54):
                    writer.writerow([number, "Text", f"Title {number}", "en", "Author"])
            posts = []
            rights = Path(folder) / "rights.tar.bz2"
            rights.touch()

            class FakeAPI:
                def __init__(self, _base, _token):
                    pass

                def post(self, path, payload):
                    posts.append((path, payload))

            with patch.object(source_catalog, "JobAPI", FakeAPI):
                with patch.object(source_catalog, "verified_books", side_effect=lambda rows, _archive: (
                        {**row, "epub_url": f"https://www.gutenberg.org/ebooks/{row['source_id'].split(':')[1]}.epub.images"}
                        for row in rows)):
                    self.assertEqual(source_catalog.main(["--catalog", str(catalog), "--rights-archive",
                                                          str(rights), "--api-base",
                                                          "https://example.com/api/v1", "--max-books", "51"]), 0)
            self.assertEqual([len(body["books"]) for _, body in posts], [50, 1])
            self.assertTrue(all(path == "/internal/source-books/sync" for path, _ in posts))

    def test_rejects_oversized_catalog_before_parsing(self):
        with tempfile.TemporaryDirectory() as folder:
            catalog = Path(folder) / "books.csv"
            with catalog.open("wb") as output:
                output.truncate(65 * 1024 * 1024)
            with self.assertRaisesRegex(ValueError, "size limit"):
                list(source_catalog.catalog_books(catalog))

    def test_books_cli_dispatches_catalog_import(self):
        from openshelf.pipeline import books
        with patch.object(source_catalog, "main", return_value=0) as importer:
            self.assertEqual(books.main(["sync-catalog", "--catalog", "feed.csv.gz",
                                         "--rights-archive", "rights.tar.bz2", "--api-base",
                                         "https://example.com/api/v1", "--dry-run"]), 0)
        self.assertIn("--rights-archive", importer.call_args.args[0])

    def test_rights_check_accepts_only_explicit_public_domain_with_epub(self):
        books = [{"source_id": f"gutenberg:{n}"} for n in (11, 12, 13, 14)]
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "rights.tar.bz2"
            with tarfile.open(archive, "w:bz2") as output:
                for number, rights, url in (
                    (11, "Public domain in the USA.", "https://www.gutenberg.org/ebooks/11.epub.images"),
                    (12, "Copyrighted.", "https://www.gutenberg.org/ebooks/12.epub.images"),
                    (13, "Unknown", "https://www.gutenberg.org/ebooks/13.epub.images"),
                    (14, "Public domain in the USA.", "https://evil.example/14.epub"),
                ):
                    data = self.rdf(number, rights, url)
                    info = tarfile.TarInfo(f"cache/epub/{number}/pg{number}.rdf")
                    info.size = len(data)
                    output.addfile(info, io.BytesIO(data))
            self.assertEqual(list(source_catalog.verified_books(books, archive)), [
                {**books[0], "epub_url": "https://www.gutenberg.org/ebooks/11.epub.images"}])
