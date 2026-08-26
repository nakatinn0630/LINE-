"""In-memory text extraction from uploaded documents.

Everything here works on `bytes` through `io.BytesIO`. Nothing is written to a
temporary directory at any point - the plaintext of a document exists only in
this process's memory, and only long enough to be summarised and re-encrypted.
"""

from __future__ import annotations

import io
import logging
import mimetypes

logger = logging.getLogger(__name__)

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

SUPPORTED_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".tsv", ".json", ".log", ".yaml", ".yml"}


class ExtractionError(RuntimeError):
    """Raised when a document is recognised but cannot be parsed."""


def guess_mime(filename: str, payload: bytes) -> str:
    """Best-effort MIME type, preferring magic bytes over the file extension."""
    sniffed = sniff_mime(payload)
    if sniffed:
        return sniffed
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def sniff_mime(payload: bytes) -> str | None:
    """Identify a format from its magic bytes. LINE does not send us a MIME type."""
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    if payload.startswith(b"%PDF-"):
        return PDF_MIME
    # DOCX is a zip; only trust the extension for the exact flavour.
    return None


def extract_text(payload: bytes, *, mime: str, filename: str) -> str | None:
    """Return the document's text, or None if this type carries no extractable text."""
    suffix = _suffix(filename)

    if mime == PDF_MIME or suffix == ".pdf":
        return _extract_pdf(payload)
    if mime == DOCX_MIME or suffix == ".docx":
        return _extract_docx(payload)
    if mime.startswith("text/") or suffix in _TEXT_SUFFIXES:
        return payload.decode("utf-8", errors="replace")
    return None


def _extract_pdf(payload: bytes) -> str:
    from pypdf import PdfReader
    from pypdf.errors import PyPdfError

    try:
        reader = PdfReader(io.BytesIO(payload))
        # An empty password unlocks most "protected" PDFs; if it does not,
        # say so rather than returning a page of nothing.
        if reader.is_encrypted and reader.decrypt("") == 0:
            raise ExtractionError("the PDF is password protected")
        pages = [page.extract_text() or "" for page in reader.pages]
    except ExtractionError:
        raise
    except PyPdfError as exc:
        raise ExtractionError(f"could not read the PDF: {exc}") from exc

    return "\n\n".join(page.strip() for page in pages if page.strip())


def _extract_docx(payload: bytes) -> str:
    import zipfile

    import docx
    from docx.opc.exceptions import PackageNotFoundError

    try:
        document = docx.Document(io.BytesIO(payload))
    except (PackageNotFoundError, zipfile.BadZipFile, ValueError, KeyError) as exc:
        # A .docx is a zip; a truncated or mislabelled file surfaces as BadZipFile.
        raise ExtractionError(f"could not read the Word document: {exc}") from exc

    blocks = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                blocks.append(" | ".join(cells))
    return "\n".join(blocks)


def _suffix(filename: str) -> str:
    _, _, tail = filename.rpartition(".")
    return f".{tail.lower()}" if tail and tail != filename else ""
