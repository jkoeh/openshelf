import hashlib
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from openshelf.pipeline.owner_documents import InvalidOwnerDocument, create_epub, extract_text


class OwnerDocumentTests(unittest.TestCase):
    def test_txt_retains_original_script_and_paragraph_order(self):
        self.assertEqual(extract_text("紅樓夢，Hello！\n\n第二段。".encode(), "txt"), [("Text", "紅樓夢，Hello！\n\n第二段。")])
        for data in (b"", b"\xff", b"text\0binary"):
            with self.assertRaises(InvalidOwnerDocument): extract_text(data, "txt")
        with self.assertRaises(InvalidOwnerDocument): extract_text(b"random binary", "doc")

    def test_docx_preserves_paragraph_and_table_order_and_rejects_entities(self):
        def docx(xml):
            result = io.BytesIO()
            with zipfile.ZipFile(result, "w") as archive: archive.writestr("word/document.xml", xml)
            return result.getvalue()
        ns = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        data = docx(f'<w:document {ns}><w:body><w:p><w:r><w:t>第一段</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>表格</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:p><w:r><w:t>Last.</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(extract_text(data, "docx"), [("Document", "第一段\n\n表格\n\nLast.")])
        nested = docx(f'<w:document {ns}><w:body><w:p><w:r><w:t>Before</w:t></w:r><w:r><w:drawing><w:txbxContent><w:p><w:r><w:t>Box</w:t></w:r></w:p></w:txbxContent></w:drawing></w:r><w:r><w:t>After</w:t></w:r></w:p></w:body></w:document>')
        self.assertEqual(extract_text(nested, "docx"), [("Document", "Before\n\nBox\n\nAfter")])
        with self.assertRaises(InvalidOwnerDocument): extract_text(docx('<!DOCTYPE document [<!ENTITY x "bad">]><document/>'), "docx")
        with self.assertRaises(InvalidOwnerDocument): extract_text(docx('<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE document [<!ENTITY x "bad">]><document>&x;</document>'.encode("utf-16")), "docx")
        with self.assertRaises(InvalidOwnerDocument): extract_text(b"PK\x03\x04invalid", "docx")

    def test_archive_and_text_budgets_fail_before_parsing(self):
        from openshelf.pipeline import owner_documents
        with patch.object(owner_documents, "MAX_TEXT", 8):
            with self.assertRaises(InvalidOwnerDocument): extract_text(b"Too much text", "txt")
        with patch.object(owner_documents, "MAX_BYTES", 8):
            with self.assertRaises(InvalidOwnerDocument): extract_text(b"Too much text", "pdf")
        with patch("zipfile.ZipFile.infolist", return_value=[SimpleNamespace(file_size=65 * 1024 * 1024)]):
            memory = io.BytesIO()
            with zipfile.ZipFile(memory, "w"): pass
            with self.assertRaisesRegex(InvalidOwnerDocument, "archive exceeds"): extract_text(memory.getvalue(), "docx")

    def test_pdf_extracts_actual_page_text_in_order_and_rejects_scanned_encrypted(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
        writer = PdfWriter()
        for text in ("First page", "Second page"):
            page = writer.add_blank_page(width=300, height=300)
            font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
            page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
            stream = DecodedStreamObject(); stream.set_data(f"BT /F1 12 Tf 20 200 Td ({text}) Tj ET".encode())
            page[NameObject("/Contents")] = stream
        result = io.BytesIO(); writer.write(result)
        self.assertEqual(extract_text(result.getvalue(), "pdf"), [("Page 1", "First page"), ("Page 2", "Second page")])
        blank = PdfWriter(); blank.add_blank_page(width=100, height=100)
        result = io.BytesIO(); blank.write(result)
        with self.assertRaisesRegex(InvalidOwnerDocument, "no extractable text"): extract_text(result.getvalue(), "pdf")
        blank.encrypt("secret"); result = io.BytesIO(); blank.write(result)
        with self.assertRaisesRegex(InvalidOwnerDocument, "encrypted"): extract_text(result.getvalue(), "pdf")

    def test_pdf_decoded_stream_limit_precedes_content_parsing(self):
        import zlib
        from pypdf import PdfWriter
        from pypdf.generic import EncodedStreamObject, NameObject
        writer = PdfWriter(); page = writer.add_blank_page(width=100, height=100)
        stream = EncodedStreamObject()
        stream._data = zlib.compress(b"q\n" * (9 * 1024 * 1024))
        stream[NameObject("/Filter")] = NameObject("/FlateDecode")
        page[NameObject("/Contents")] = stream
        data = io.BytesIO(); writer.write(data)
        self.assertLess(len(data.getvalue()), 16 * 1024 * 1024)
        with self.assertRaisesRegex(InvalidOwnerDocument, "resource limits"):
            extract_text(data.getvalue(), "pdf")

    def test_generated_epub_is_deterministic_and_has_identity_language_and_order(self):
        from ebooklib import epub
        data = "天地玄黃。\n\n第二段，Hello 世界！".encode()
        source_id = "ownerupload:11111111-1111-4111-8111-111111111111"
        with tempfile.TemporaryDirectory() as folder:
            first, second = Path(folder) / "one.epub", Path(folder) / "two.epub"
            for path in (first, second):
                create_epub(data, "txt", path, source_id=source_id, title="測試", author="Owner", language="zh", sha256=hashlib.sha256(data).hexdigest())
            self.assertEqual(first.read_bytes(), second.read_bytes())
            book = epub.read_epub(str(first))
            self.assertEqual(book.get_metadata("DC", "identifier")[0][0], "urn:openshelf:" + source_id)
            self.assertEqual(book.get_metadata("DC", "language")[0][0], "zh")
            html = list(book.get_items_of_type(9))[0].get_content().decode()
            self.assertLess(html.index("天地玄黃"), html.index("第二段"))
            self.assertNotIn("Public domain", book.get_metadata("DC", "rights")[0][0])
            from openshelf.pipeline.owner_documents import validate_owner_epub
            validate_owner_epub(first, source_id, hashlib.sha256(data).hexdigest(), title="測試", author="Owner", language="zh")
            with self.assertRaises(InvalidOwnerDocument): validate_owner_epub(first, source_id, "wrong", title="測試", author="Owner", language="zh")
            with self.assertRaises(InvalidOwnerDocument): validate_owner_epub(first, source_id, hashlib.sha256(data).hexdigest(), title="Other edition", author="Owner", language="zh")
            with self.assertRaisesRegex(InvalidOwnerDocument, "hash mismatch"):
                create_epub(data, "txt", first, source_id=source_id, title="T", author="A", language="zh", sha256="wrong")

    def test_real_extraction_subprocess_renews_lease_and_keeps_original_private(self):
        from openshelf.pipeline import job_consumer
        data = b"First paragraph.\n\nSecond paragraph."
        class API:
            def __init__(self): self.posts = []
            def post(self, path, body): self.posts.append((path, body))
            def download_owner_document(self, upload_id): return data
        api = API()
        job = {"id": "job", "lease_token": "lease", "source_id": "ownerupload:11111111-1111-4111-8111-111111111111", "title": "Document", "author": "Owner", "language": "en", "provenance": {"upload_id": "11111111-1111-4111-8111-111111111111", "format": "txt", "sha256": hashlib.sha256(data).hexdigest()}}
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "document.epub"
            job_consumer.prepare_owner_epub(api, job, path)
            self.assertTrue(path.exists())
            self.assertFalse(path.with_suffix(".original").exists())
            self.assertFalse(path.with_suffix(".pending.epub").exists())
            self.assertEqual(len(api.posts), 3)

    def test_consumer_routes_owner_documents_without_asserting_public_domain(self):
        from openshelf.pipeline import job_consumer
        from openshelf.pipeline.standard_ebooks_source import title_slug
        for language, engine, python in [("en", "kokoro", None), ("zh", "qwen3", "mandarin-python")]:
            data = "Original text，中文。".encode()
            source_id = "ownerupload:11111111-1111-4111-8111-111111111111"
            proof = {"upload_id": source_id.split(":")[1], "format": "txt", "sha256": hashlib.sha256(data).hexdigest(), "rights_acknowledged": True, "public_publication_acknowledged": True}
            job = {"id": "job", "lease_token": "lease", "source_id": source_id, "title": "Document", "author": "Owner", "language": language, "provenance": proof, "build_id": "0123456789abcdef"}
            api = SimpleNamespace(base="https://example.com/api/v1", token="test-pc-token-longer-than-twenty-four", post=MagicMock())
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                path = root / "local" / "owner" / (title_slug(job["title"], source_id) + ".epub")
                create_epub(data, "txt", path, source_id=source_id, title="Document", author="Owner", language=language, sha256=proof["sha256"])
                with patch.object(job_consumer, "verify_public_domain") as rights, patch.object(job_consumer, "check_epub_rights") as embedded, patch.object(job_consumer.subprocess, "Popen", return_value=SimpleNamespace(returncode=0, poll=lambda: 0)) as launch:
                    job_consumer.process_job(api, job, download_root=root, audio_root=root / "audio", device="cpu", mandarin_python=python, english_python="english-python")
                rights.assert_not_called(); embedded.assert_not_called()
                command = launch.call_args.args[0]
                self.assertEqual(command[command.index("--engine") + 1], engine)
                self.assertEqual(command[0], "english-python" if language == "en" else "mandarin-python")
                self.assertEqual(launch.call_args.kwargs["env"]["OPENSHELF_REQUIRE_COMPLETE_AUDIO"], "1")
                self.assertEqual(launch.call_args.kwargs["env"]["OPENSHELF_API_BASE"], api.base)
                self.assertEqual(launch.call_args.kwargs["env"]["OPENSHELF_PC_TOKEN"], api.token)
                self.assertEqual(api.post.call_args.args[1]["success"], True)
                self.assertEqual(api.post.call_args.args[1]["title_slug"], path.stem)

    def test_process_cleanup_waits_and_kills_unresponsive_descendants(self):
        from openshelf.pipeline import job_consumer
        root, descendant, child = MagicMock(), MagicMock(), MagicMock()
        root.children.return_value = [descendant]
        with patch("psutil.Process", return_value=root), patch("psutil.wait_procs", side_effect=[([root], [descendant]), ([descendant], [])]):
            job_consumer.terminate_child_tree(child)
        root.children.assert_called_once_with(recursive=True)
        root.terminate.assert_called_once(); descendant.terminate.assert_called_once()
        descendant.kill.assert_called_once(); child.wait.assert_called_once_with(timeout=20)

    def test_extraction_timeout_stops_entire_tree_and_removes_temporary_files(self):
        from openshelf.pipeline import job_consumer
        import subprocess
        data = b"Text"
        api = SimpleNamespace(post=MagicMock(), download_owner_document=lambda _: data)
        job = {"id": "job", "lease_token": "lease", "source_id": "ownerupload:11111111-1111-4111-8111-111111111111", "title": "Document", "author": "Owner", "language": "en", "provenance": {"upload_id": "11111111-1111-4111-8111-111111111111", "format": "txt", "sha256": hashlib.sha256(data).hexdigest()}}
        child = MagicMock(); child.communicate.side_effect = subprocess.TimeoutExpired("extract", 60)
        with tempfile.TemporaryDirectory() as folder, patch.object(job_consumer.subprocess, "Popen", return_value=child), patch.object(job_consumer, "terminate_child_tree") as terminate:
            path = Path(folder) / "document.epub"
            with self.assertRaisesRegex(InvalidOwnerDocument, "60 seconds"): job_consumer.prepare_owner_epub(api, job, path)
            terminate.assert_called_once_with(child)
            self.assertFalse(path.with_suffix(".original").exists())
            self.assertFalse(path.exists())

    def test_optional_english_runtime_is_validated_without_loading_models(self):
        from openshelf.pipeline import job_consumer
        with patch.dict("os.environ", {"OPENSHELF_ENGLISH_PYTHON": ""}):
            self.assertIsNone(job_consumer.english_runtime())
        with tempfile.TemporaryDirectory() as folder:
            runtime = Path(folder) / "python.exe"; runtime.touch()
            with patch.dict("os.environ", {"OPENSHELF_ENGLISH_PYTHON": str(runtime)}), patch.object(job_consumer.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as probe:
                self.assertEqual(job_consumer.english_runtime(), str(runtime))
                self.assertEqual(probe.call_args.kwargs["timeout"], 30)
                self.assertIn("(3,12)", probe.call_args.args[0][2])
                probe.return_value.returncode = 1
                with self.assertRaises(ValueError): job_consumer.english_runtime()

    def test_invalid_english_runtime_does_not_advertise_standard_english(self):
        from openshelf.pipeline import job_consumer
        api = SimpleNamespace(post=MagicMock(return_value={"job": None}))
        with patch.object(job_consumer, "JobAPI", return_value=api), patch.object(job_consumer, "english_runtime", side_effect=ValueError("unavailable")), patch.object(job_consumer, "mandarin_runtime", return_value="mandarin-python"), patch.object(job_consumer, "document_runtime_ready", return_value=True):
            self.assertEqual(job_consumer.main(["--api-base", "https://example.com/api/v1", "--once"]), 0)
        capabilities = api.post.call_args.args[1]
        self.assertFalse(capabilities["english"])
        self.assertTrue(capabilities["mandarin"])


if __name__ == "__main__": unittest.main()
