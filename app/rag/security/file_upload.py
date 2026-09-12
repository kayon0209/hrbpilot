"""HRBP AI Workbench — upload file validation (security gate).

Defense-in-depth for the KB upload path (P1 §5.4):

  - extension whitelist (caller-visible, matches the parser's support)
  - declared MIME cross-check against the extension
  - magic-number sniffing: the file's real format must match what the
    extension claims — a renamed executable/polyglot never reaches MinIO
  - size cap and page-count cap for PDFs
  - decompression-bomb guard: a small PDF may expand to hundreds of MB of
    text; the parsed-text volume is capped before the chunker runs
  - filename sanitization: the object key is built from a cleaned name so
    ``../`` traversal, absolute paths, and control characters cannot shape
    the storage key

Every rejection raises ValidationError with an operator-readable message;
nothing here silently degrades.
"""

from __future__ import annotations

import re

from app.shared.errors import ValidationError

# Extension → expected declared-MIME prefixes (the magic-byte rules are
# separate functions — see validate_magic).
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
MAX_PARSED_TEXT_CHARS = 4_000_000
PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"

_FORBIDDEN_FILENAME = re.compile(r"[\x00-\x1f<>:\"|?*\\]")


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
    benefit of the doubt here — the magic-number check below is the real
    format gate.
    """
    expected = ALLOWED_TYPES[ext]
    mime = (declared_mime or "").split(";")[0].strip().lower()
    if mime and mime not in expected:
        raise ValidationError(f"文件声明的 MIME 类型 ({mime}) 与扩展名 .{ext} 不符")


def validate_magic(ext: str, content: bytes) -> None:
    """The file's real format must match its extension.

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
    validate_magic(ext, content)
    if ext == "pdf":
        validate_pdf_pages(content)

    return sanitize_filename(filename)


__all__ = [
    "ALLOWED_TYPES",
    "MAX_PARSED_TEXT_CHARS",
    "MAX_UPLOAD_BYTES",
    "sanitize_filename",
    "validate_extension",
    "validate_magic",
    "validate_mime",
    "validate_pdf_pages",
    "validate_upload",
]
