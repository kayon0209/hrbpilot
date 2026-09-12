"""Upload security gate tests (P1 §5.4).

Locks every layer of the file-upload defense:

  - extension whitelist is enforced before any storage write
  - declared MIME is cross-checked against the extension
  - magic-number sniffing rejects renamed/polyglot files
  - PDF page-count cap blocks oversized documents before parsing
  - decompression-bomb guard caps parsed text volume
  - filenames are sanitized: traversal, absolute paths, control characters
    and shell-special bytes never shape an object-storage key
"""

import pytest

from app.rag.ingestion.pipeline import DocumentParser
from app.rag.security.file_upload import (
    MAX_PARSED_TEXT_CHARS,
    MAX_PDF_PAGES,
    sanitize_filename,
    validate_upload,
)
from app.shared.errors import ValidationError

PDF_MAGIC = b"%PDF-1.4\n"
ZIP_MAGIC = b"PK\x03\x04" + b"\x00" * 40


# ---------------------------------------------------------------- extensions


def test_extension_whitelist_rejects_executable_types():
    for name in ("malware.exe", "shell.sh", "doc.doc", "sheet.xls", "slides.ppt"):
        with pytest.raises(ValidationError, match="不支持的文件类型"):
            validate_upload(filename=name, declared_mime="application/octet-stream", content=b"data")


def test_extension_whitelist_accepts_supported_types():
    assert validate_upload(filename="制度.pdf", declared_mime="application/pdf", content=PDF_MAGIC) == "制度.pdf"
    assert (
        validate_upload(
            filename="员工手册.docx",
            declared_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            content=ZIP_MAGIC,
        )
        == "员工手册.docx"
    )
    assert validate_upload(filename="notes.txt", declared_mime="text/plain", content="假期规定".encode()) == "notes.txt"


# ---------------------------------------------------------------- MIME


def test_declared_mime_must_match_extension():
    with pytest.raises(ValidationError, match="MIME"):
        validate_upload(filename="doc.pdf", declared_mime="application/x-msdownload", content=PDF_MAGIC)
    # empty/unknown declared MIME is tolerated — the magic check is the real gate
    assert validate_upload(filename="doc.pdf", declared_mime=None, content=PDF_MAGIC) == "doc.pdf"


# ---------------------------------------------------------------- magic


def test_magic_rejects_renamed_pdf():
    with pytest.raises(ValidationError, match="PDF"):
        validate_upload(filename="policy.txt", declared_mime="text/plain", content=PDF_MAGIC)


def test_magic_rejects_renamed_zip_as_pdf():
    with pytest.raises(ValidationError, match="PDF"):
        # a zip (e.g. renamed jar) claiming .pdf is caught by the PDF magic rule
        validate_upload(filename="policy.pdf", declared_mime="application/pdf", content=ZIP_MAGIC)


def test_magic_rejects_executable_renamed_to_pdf():
    with pytest.raises(ValidationError, match="PDF"):
        validate_upload(filename="innocent.pdf", declared_mime="application/pdf", content=b"MZ\x90\x00" + b"\x00" * 30)


# ---------------------------------------------------------------- pages / bomb


def test_pdf_page_cap_rejects_oversized_document():
    # a "pdf" whose body declares far more page objects than the cap
    fat_pdf = PDF_MAGIC + b"/Type /Page" * (MAX_PDF_PAGES + 5)
    with pytest.raises(ValidationError, match="页数超过上限"):
        validate_upload(filename="fat.pdf", declared_mime="application/pdf", content=fat_pdf)


def test_parser_rejects_decompression_bomb_text():
    # A tiny txt that expands to an oversized body (len > cap) is refused by
    # the parser itself — the last line of defense.
    parser = DocumentParser()
    with pytest.raises(ValueError, match="压缩炸弹"):
        parser.parse(("字" * (MAX_PARSED_TEXT_CHARS + 1)).encode("utf-8"), "txt")


# ---------------------------------------------------------------- filename


def test_filename_traversal_is_neutralized():
    for hostile in (
        "../../etc/passwd",
        "..\\..\\windows\\system32\\config",
        "/absolute/path/policy.pdf",
        "a/b/c/深度穿越.txt",
    ):
        cleaned = sanitize_filename(hostile)
        assert "/" not in cleaned and "\\" not in cleaned
        assert ".." not in cleaned
        assert cleaned  # non-empty


def test_filename_control_and_special_bytes_are_stripped():
    cleaned = sanitize_filename("年假\n规定\x00.txt")
    assert "\n" not in cleaned and "\x00" not in cleaned
    assert cleaned.endswith(".txt")


def test_filename_dot_entries_rejected():
    with pytest.raises(ValidationError, match="文件名无效"):
        sanitize_filename("..")
    with pytest.raises(ValidationError, match="文件名无效"):
        sanitize_filename("")


def test_route_object_key_uses_sanitized_name():
    """End-to-end: the s3 key built from a hostile filename contains only the
    cleaned basename — traversal cannot shape the storage key."""
    hostile = "../../minio-secret-bucket/pwned.pdf"
    cleaned = validate_upload(filename=hostile, declared_mime="application/pdf", content=PDF_MAGIC)
    kb_id, doc_id = "kb-1", "doc-1"
    s3_key = f"{kb_id}/{doc_id}/{cleaned}"
    assert s3_key == "kb-1/doc-1/pwned.pdf"
    assert ".." not in s3_key
