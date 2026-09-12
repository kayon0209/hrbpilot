"""HRBP AI Workbench — upload file validation (security gate).

Defense-in-depth for the KB upload path (P1 §5.4), hardened in batch #1:

  - extension whitelist (caller-visible, matches the parser's support)
  - declared-MIME cross-check against the extension (cheap, browser-provided)
  - CONTENT-BASED MIME detection (libmagic if available, else a built-in
    signature table) — the real format is sniffed from the bytes, not
    trusted from the extension or Content-Type header
  - magic-number validation: the sniffed/declared format must match what the
    extension claims; a renamed executable/polyglot/office file never reaches
    MinIO
  - size cap and page-count cap for PDFs
  - structural PDF decompression-bomb guard (parser-independent): the file may
    advertise an absurd object count or nest many compressed object streams;
    both are rejected before any parse runs
  - filename sanitization: the object key is built from a cleaned name so
    ``../`` traversal, absolute paths, and control characters cannot shape the
    storage key
  - build_object_key(): defense-in-depth assembly of the MinIO key

Every rejection raises ValidationError with an operator-readable message;
nothing here silently degrades.
"""

from __future__ import annotations

import re

from app.shared.errors import ValidationError

# Extension -> expected declared-MIME prefixes (the magic-byte rules are
# separate functions — see validate_magic / detect_content_mime).
ALLOWED_TYPES: dict[str, tuple[str, ...]] = {
    "txt": ("text/plain", "application/octet-stream", ""),
    "pdf": ("application/pdf", "application/octet-stream", ""),
    "docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
        "",
    ),
}

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # mirrors the route's existing cap
MAX_PDF_PAGES = 500
# Cap the text a single document may expand into (chars). Parsed text well
# above this is a decompression-bomb signature, not a legitimate policy doc.
# Kept here because app/rag/ingestion/pipeline.py imports it for the parser cap.
MAX_PARSED_TEXT_CHARS = 4_000_000
PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"

# --- structural PDF bomb thresholds (parser-independent) -------------------
# A legitimate policy PDF at 20 MB has at most tens of thousands of objects and
# a few hundred object streams. These caps reject pathological inputs before
# any parse runs.
MAX_PDF_OBJECTS = 200_000          # hard cap on /Size (cross-reference count)
MAX_PDF_OBJSTM = 5_000             # nested/compressed object streams
MAX_PDF_FLATE = 20_000             # FlateDecode stream objects
# Compression-ratio guard: if the declared object count would average fewer
# than this many bytes per object, the file is lying about its size (classic
# bomb). A small real policy PDF has far more than this.
MIN_BYTES_PER_PDF_OBJECT = 50

_FORBIDDEN_FILENAME = re.compile(r"[\x00-\x1f<>:\"|?*\\]")

# Content-type signature table (fallback when libmagic is unavailable).
# Maps a leading-byte signature to a detected MIME. Order matters: the first
# match wins, so put the more specific signatures first.
_CONTENT_SIGNATURES: list[tuple[bytes, str]] = [
    (b"%PDF-", "application/pdf"),
    (b"PK\x03\x04", "application/zip"),
    (b"PK\x05\x06", "application/zip"),   # empty archive
    (b"PK\x07\x08", "application/zip"),   # spanned archive
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"RIFF", "application/x-riff"),
    (b"\x7fELF", "application/x-elf"),
    (b"MZ", "application/x-dosexec"),
    (b"\xd0\xcf\x11\xe0", "application/x-ole-storage"),  # old .doc/.xls/.ppt
    (b"{\rtf", "application/rtf"),
]

# For each allowed extension, the set of detected MIME types that are
# consistent with it. An empty string means "could not be determined" — which
# is tolerated because the magic-number check below is the real format gate.
# application/octet-stream is tolerated too (libmagic sometimes returns it for
# otherwise-valid office files); the magic check still has to pass.
_ALLOWED_DETECTED: dict[str, set[str]] = {
    "pdf": {"application/pdf", "application/octet-stream", ""},
    "docx": {
        "application/zip",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/octet-stream",
        "",
    },
    "txt": {"text/plain", "application/octet-stream", ""},
}

_PDF_PAGE_RE = re.compile(rb"/Type\s*/Page(?!s)")
_PDF_SIZE_RE = re.compile(rb"/Size\s+(\d+)")
_PDF_OBJSTM_RE = re.compile(rb"/ObjStm")
_PDF_FLATE_RE = re.compile(rb"/FlateDecode")


def validate_extension(filename: str) -> str:
    """Return the lowercased extension if it is in the whitelist."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_TYPES:
        raise ValidationError(
            f"不支持的文件类型 .{ext or '(无扩展名)'}；仅支持 {', '.join(sorted(ALLOWED_TYPES))}"
        )
    return ext


def validate_mime(ext: str, declared_mime: str | None) -> None:
    """The declared Content-Type must be consistent with the extension.

    Browsers usually send a reasonable MIME; empty/unknown senders get the
    benefit of the doubt here — the content-based checks below are the real
    format gate.
    """
    expected = ALLOWED_TYPES[ext]
    mime = (declared_mime or "").split(";")[0].strip().lower()
    if mime and mime not in expected:
        raise ValidationError(f"文件声明的 MIME 类型 ({mime}) 与扩展名 .{ext} 不符")


def detect_content_mime(content: bytes) -> str:
    """Sniff the real content type from the bytes.

    Uses libmagic when the ``magic`` package (and its C library) is available;
    otherwise falls back to a built-in signature table. Returns a lowercased
    MIME string, or an empty string when the content is ambiguous text that no
    signature matches.
    """
    # libmagic path (preferred — knows OOXML, jar, executable, image subtypes).
    try:  # pragma: no cover - exercised only when libmagic is installed
        import magic  # type: ignore

        with magic.Magic(raw=True, mime=True) as m:
            detected = m.from_buffer(content[: 1 << 20])  # sample first 1 MiB
        return (detected or "").split(";")[0].strip().lower()
    except Exception:
        pass
    # signature-table fallback
    for sig, mime in _CONTENT_SIGNATURES:
        if content.startswith(sig):
            return mime
    if _looks_like_text(content):
        return "text/plain"
    return ""


def _looks_like_text(content: bytes) -> bool:
    """Heuristic: valid UTF-8 (or mostly-printable latin-1) with no control junk."""
    if not content:
        return False
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        sample = content[:4096]
        return all(32 <= b < 127 or b in (9, 10, 13) for b in sample)
    return not any(0 <= ord(c) < 32 and c not in "\t\n\r" for c in text[:4096])


def validate_content_type(ext: str, detected_mime: str) -> None:
    """The sniffed real type must be consistent with the extension.

    A renamed executable / image / OLE / RTF / archive claiming a KB extension
    is rejected here — this is the content-level "MIME 探测" gate that the
    declared-MIME check alone cannot provide.
    """
    allowed = _ALLOWED_DETECTED.get(ext, set())
    if detected_mime and detected_mime not in allowed:
        raise ValidationError(
            f"文件内容被识别为 {detected_mime}，与扩展名 .{ext} 不符，已拒绝入库"
        )


def validate_magic(ext: str, content: bytes) -> None:
    """The file's real format must match its extension (byte-level).

    - pdf: must start with ``%PDF-``
    - docx: must start with the ZIP local-file-header magic (OOXML is zipped)
    - txt: must not start with another allowed format's magic (a renamed
      pdf/docx goes to the right parser only if the extension is honest)
    """
    if ext == "pdf" and not content.startswith(PDF_MAGIC):
        raise ValidationError("文件内容不是有效的 PDF（缺少 %PDF- 魔数）")
    if ext == "docx" and not content.startswith(ZIP_MAGIC):
        raise ValidationError("文件内容不是有效的 DOCX（缺少 OOXML/ZIP 魔数）")
    if ext == "txt" and (content.startswith(PDF_MAGIC) or content.startswith(ZIP_MAGIC)):
        raise ValidationError("文件内容是 PDF/DOCX 格式但扩展名为 .txt，请修正扩展名后重传")


def validate_pdf_pages(content: bytes) -> None:
    """Cheap page-count guard BEFORE any parse: scans for page objects.

    ``pypdf`` counting pages on a malicious file can itself be expensive;
    a raw ``/Type /Page`` occurrence count is cheap and proportional to
    what the parser will eventually iterate.
    """
    try:
        approx_pages = content.count(b"/Type /Page") + content.count(b"/Type/Page")
    except Exception as exc:  # pragma: no cover - bytes.count cannot fail
        raise ValidationError("PDF 结构无法解析") from exc
    if approx_pages > MAX_PDF_PAGES:
        raise ValidationError(f"PDF 页数超过上限 {MAX_PDF_PAGES} 页，请拆分后上传")


def validate_pdf_bomb(content: bytes) -> None:
    """Reject PDFs that would explode during parsing — parser-independent.

    Cheap string scans on the raw bytes; no pypdf involved. Catches:
      - an absurd cross-reference object count (``/Size N``)
      - a compression-ratio lie (declared objects vs file size)
      - too many nested/compressed object streams (``/ObjStm``)
      - too many FlateDecode stream objects
    """
    # nested/compressed object streams (the usual bomb vector)
    objstm = len(_PDF_OBJSTM_RE.findall(content))
    if objstm > MAX_PDF_OBJSTM:
        raise ValidationError(f"PDF 压缩对象流过多 ({objstm})，疑似压缩炸弹")
    # FlateDecode stream objects
    flate = len(_PDF_FLATE_RE.findall(content))
    if flate > MAX_PDF_FLATE:
        raise ValidationError(f"PDF FlateDecode 流过多 ({flate})，疑似压缩炸弹")
    # cross-reference object count from /Size
    size_match = _PDF_SIZE_RE.search(content)
    if size_match:
        obj_count = int(size_match.group(1))
        if obj_count > MAX_PDF_OBJECTS:
            raise ValidationError(f"PDF 对象数超过上限 {MAX_PDF_OBJECTS}，疑似压缩炸弹")
        # ratio guard: declared objects needing far more bytes than the file
        # has is impossible for a legitimate document.
        if obj_count > max(1, len(content)) // MIN_BYTES_PER_PDF_OBJECT * 4:
            raise ValidationError("PDF 声明对象数与文件大小不符，疑似压缩炸弹")


def sanitize_filename(filename: str) -> str:
    """Clean an untrusted filename before it shapes an object-storage key.

    Strips paths (``../`` traversal, absolute paths), control characters and
    shell-special bytes; collapses the result. The returned name is safe to
    embed in ``{kb}/{doc}/{name}`` keys.
    """
    name = (filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    name = _FORBIDDEN_FILENAME.sub("_", name).strip("._")
    if not name or name in {".", ".."}:
        raise ValidationError("文件名无效：缺少安全可用的文件名部分")
    return name[:180]


def build_object_key(kb_id: str, doc_id: str, sanitized_name: str) -> str:
    """Defense-in-depth assembly of the MinIO object key.

    Re-asserts that no path component can shape the key: ``kb_id`` / ``doc_id``
    are server-generated, but a future caller change must not reintroduce
    traversal — so we reject path characters in every component before joining.
    """
    for part in (kb_id, doc_id):
        if not part or "/" in part or "\\" in part or ".." in part:
            raise ValidationError("存储桶路径非法：kb/doc 标识含路径字符")
    name = sanitized_name or "document"
    if "/" in name or "\\" in name or ".." in name:
        raise ValidationError("存储桶对象键含路径穿越字符")
    return f"{kb_id}/{doc_id}/{name}"


def validate_upload(
    *,
    filename: str,
    declared_mime: str | None,
    content: bytes,
) -> str:
    """Full upload gate. Returns the sanitized filename on success."""
    if not content:
        raise ValidationError("文件内容为空")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ValidationError(f"文件超过大小限制 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")

    ext = validate_extension(filename)
    validate_mime(ext, declared_mime)
    detected = detect_content_mime(content)
    validate_content_type(ext, detected)
    validate_magic(ext, content)
    if ext == "pdf":
        validate_pdf_pages(content)
        validate_pdf_bomb(content)

    return sanitize_filename(filename)


__all__ = [
    "ALLOWED_TYPES",
    "MAX_PARSED_TEXT_CHARS",
    "MAX_PDF_PAGES",
    "MAX_UPLOAD_BYTES",
    "build_object_key",
    "detect_content_mime",
    "sanitize_filename",
    "validate_content_type",
    "validate_extension",
    "validate_magic",
    "validate_mime",
    "validate_pdf_bomb",
    "validate_pdf_pages",
    "validate_upload",
]
