import io

import pytest

from app.extract import (
    DOCX_MIME,
    PDF_MIME,
    ExtractionError,
    extract_text,
    guess_mime,
    sniff_mime,
)


def docx_bytes(paragraph: str, table: list[str] | None = None) -> bytes:
    import docx

    document = docx.Document()
    document.add_paragraph(paragraph)
    if table:
        row = document.add_table(rows=1, cols=len(table)).rows[0]
        for cell, value in zip(row.cells, table):
            cell.text = value
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def pdf_bytes() -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.mark.parametrize(
    "payload,expected",
    [
        (b"\xff\xd8\xff\xe0rest", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n", "image/png"),
        (b"GIF89a...", "image/gif"),
        (b"RIFF1234WEBPxx", "image/webp"),
        (b"%PDF-1.7", PDF_MIME),
        (b"nothing recognisable", None),
    ],
)
def test_magic_bytes_are_trusted(payload, expected):
    assert sniff_mime(payload) == expected


def test_magic_bytes_beat_a_lying_extension():
    assert guess_mime("photo.txt", b"\x89PNG\r\n\x1a\n") == "image/png"


def test_extension_is_the_fallback():
    assert guess_mime("notes.txt", b"plain") == "text/plain"


def test_docx_paragraphs_and_tables_are_extracted():
    text = extract_text(
        docx_bytes("四半期の売上", table=["区分", "金額"]), mime=DOCX_MIME, filename="q.docx"
    )
    assert "四半期の売上" in text
    assert "区分 | 金額" in text


def test_pdf_is_parsed():
    assert extract_text(pdf_bytes(), mime=PDF_MIME, filename="x.pdf") == ""


def test_a_corrupt_pdf_reports_a_clear_error():
    with pytest.raises(ExtractionError):
        extract_text(b"%PDF-1.4 truncated", mime=PDF_MIME, filename="bad.pdf")


def test_a_corrupt_docx_reports_a_clear_error():
    with pytest.raises(ExtractionError):
        extract_text(b"not a zip at all", mime=DOCX_MIME, filename="bad.docx")


@pytest.mark.parametrize("name", ["a.txt", "a.md", "a.csv", "a.json"])
def test_plain_text_formats_decode(name):
    assert extract_text("こんにちは".encode(), mime="text/plain", filename=name) == "こんにちは"


def test_undecodable_bytes_do_not_raise():
    assert extract_text(b"\xff\xfe bad", mime="text/plain", filename="a.txt")


def test_unknown_binary_yields_no_text():
    assert extract_text(b"\x00\x01", mime="application/octet-stream", filename="a.bin") is None
