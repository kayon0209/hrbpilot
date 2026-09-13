"""Upload security gate tests (P1 §5.4, hardened in batch #1).

Locks every layer of the file-upload defense. Self-contained: it imports only
the gate module and ``app.shared.errors`` (no parser / DB), so it runs without
Docker and survives refactoring of the ingestion layer.

  - extension whitelist is enforced before any storage write
  - declared MIME is cross-checked against the extension
  - CONTENT-BASED MIME detection sniffs the real type from bytes and rejects
    renamed executables / images / archives that claim a KB extension
  - magic-number sniffing rejects renamed/polyglot files
  - PDF page-count cap blocks oversized documents before parsing
  - structural PDF decompression-bomb guard (object count, compression ratio,
    nested object streams, FlateDecode streams) blocks bombs before parsing
  - filenames are sanitized: traversal, absolute paths, control characters
    and shell-special bytes never shape an object-storage key
  - build_object_key() re-validates every path component before joining
"""

import pytest

from app.rag.security.file_upload import (
    MAX_PDF_FLATE,
    MAX_PDF_OBJSTM,
    MAX_PDF_PAGES,
    build_object_key,
    detect_content_mime,
    sanitize_filename,
    validate_content_type,
    validate_upload,
)
from app.shared.errors import ValidationError

PDF_MAGIC = b"%PDF-1.4\n"
ZIP_MAGIC = b"PK\x03\x04" + b"\x00" * 40
EXE_MAGIC = b"MZ\x90\x00" + b"\x00" * 40
PNG_MAGIC = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20


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


# ---------------------------------------------------------------- MIME (declared)


def test_declared_mime_must_match_extension():
    with pytest.raises(ValidationError, match="MIME"):
        validate_upload(filename="doc.pdf", declared_mime="application/x-msdownload", content=PDF_MAGIC)
    # empty/unknown declared MIME is tolerated — the content checks are the real gate
    assert validate_upload(filename="doc.pdf", declared_mime=None, content=PDF_MAGIC) == "doc.pdf"


# ---------------------------------------------------------- MIME (content-sniffed)


def test_content_mime_detects_real_types():
    assert detect_content_mime(PDF_MAGIC) == "application/pdf"
    assert detect_content_mime(ZIP_MAGIC) == "application/zip"
    assert detect_content_mime("假期规定".encode()) == "text/plain"
    assert detect_content_mime(EXE_MAGIC) == "application/x-dosexec"
    assert detect_content_mime(PNG_MAGIC) == "image/png"


def test_content_mime_rejects_renamed_executable_as_pdf():
    # a Windows exe renamed to .pdf is sniffed as x-dosexec and rejected
    with pytest.raises(ValidationError, match="x-dosexec"):
        validate_upload(filename="innocent.pdf", declared_mime="application/pdf", content=EXE_MAGIC)


def test_content_mime_rejects_renamed_image_as_pdf():
    with pytest.raises(ValidationError, match="image/png"):
        validate_upload(filename="pic.pdf", declared_mime="application/pdf", content=PNG_MAGIC)


def test_content_mime_rejects_renamed_archive_as_pdf():
    # a zip/jar renamed to .pdf is sniffed as application/zip, not PDF
    with pytest.raises(ValidationError, match="application/zip"):
        validate_upload(filename="data.pdf", declared_mime="application/pdf", content=ZIP_MAGIC)


def test_content_mime_accepts_zip_family_as_docx():
    # a legit .docx IS a zip; zip-family detection must accept it
    assert validate_upload(filename="员工手册.docx", declared_mime=None, content=ZIP_MAGIC) == "员工手册.docx"


def test_content_mime_validate_content_type_unit():
    # unit-level: the detected type must be consistent with the extension
    with pytest.raises(ValidationError):
        validate_content_type("pdf", "application/x-dosexec")
    with pytest.raises(ValidationError):
        validate_content_type("txt", "application/pdf")
    # tolerated unknowns
    validate_content_type("pdf", "")
    validate_content_type("pdf", "application/octet-stream")


# ---------------------------------------------------------------- magic


def test_magic_rejects_renamed_pdf():
    with pytest.raises(ValidationError, match="不符"):
        validate_upload(
            filename="policy.txt", declared_mime="text/plain", content=PDF_MAGIC
        )  # detected application/pdf vs .txt


def test_magic_rejects_renamed_zip_as_pdf():
    with pytest.raises(ValidationError, match="不符"):
        validate_upload(filename="policy.pdf", declared_mime="application/pdf", content=ZIP_MAGIC)


def test_magic_rejects_executable_renamed_to_pdf():
    with pytest.raises(ValidationError, match="不符"):
        validate_upload(filename="innocent.pdf", declared_mime="application/pdf", content=EXE_MAGIC)


# ---------------------------------------------------------------- pages / bomb


def test_pdf_page_cap_rejects_oversized_document():
    fat_pdf = PDF_MAGIC + b"/Type /Page" * (MAX_PDF_PAGES + 5)
    with pytest.raises(ValidationError, match="页数超过上限"):
        validate_upload(filename="fat.pdf", declared_mime="application/pdf", content=fat_pdf)


def test_pdf_bomb_rejected_on_object_count_and_ratio():
    # tiny file declaring a million cross-reference objects -> impossible ratio
    bomb = PDF_MAGIC + b"/Size 1000000\n" + b"x" * 200
    with pytest.raises(ValidationError, match=r"压缩炸弹|对象数"):
        validate_upload(filename="bomb.pdf", declared_mime="application/pdf", content=bomb)


def test_pdf_bomb_rejected_on_object_streams():
    bomb = PDF_MAGIC + (b"/ObjStm\n" * (MAX_PDF_OBJSTM + 10))
    with pytest.raises(ValidationError, match="压缩对象流"):
        validate_upload(filename="bomb.pdf", declared_mime="application/pdf", content=bomb)


def test_pdf_bomb_rejected_on_flatedecode_streams():
    bomb = PDF_MAGIC + (b"/FlateDecode\n" * (MAX_PDF_FLATE + 10))
    with pytest.raises(ValidationError, match="FlateDecode"):
        validate_upload(filename="bomb.pdf", declared_mime="application/pdf", content=bomb)


# ---------------------------------------------------------------- size


def test_size_cap_rejects_oversized_file():
    big = PDF_MAGIC + b"x" * (21 * 1024 * 1024)
    with pytest.raises(ValidationError, match="大小限制"):
        validate_upload(filename="big.pdf", declared_mime="application/pdf", content=big)


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


# ---------------------------------------------------------------- object key


def test_build_object_key_uses_sanitized_name_only():
    """End-to-end: the s3 key built from a hostile filename contains only the
    cleaned basename — traversal cannot shape the storage key."""
    hostile = "../../minio-secret-bucket/pwned.pdf"
    cleaned = validate_upload(filename=hostile, declared_mime="application/pdf", content=PDF_MAGIC)
    kb_id, doc_id = "kb-1", "doc-1"
    s3_key = build_object_key(kb_id, doc_id, cleaned)
    assert s3_key == "kb-1/doc-1/pwned.pdf"
    assert ".." not in s3_key


def test_build_object_key_rejects_traversal_in_ids():
    # a future caller passing a tainted kb/doc id must not reintroduce traversal
    with pytest.raises(ValidationError, match="路径"):
        build_object_key("kb/../x", "doc-1", "ok.pdf")
    with pytest.raises(ValidationError, match="路径"):
        build_object_key("kb-1", "doc\\1", "ok.pdf")
