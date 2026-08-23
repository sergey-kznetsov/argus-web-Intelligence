from io import BytesIO

from pypdf import PdfWriter

from argus.extraction.pdf import BoundedPdfExtractor


def extractor(**values) -> BoundedPdfExtractor:
    config = {
        "max_bytes": 1024 * 1024,
        "max_pages": 5,
        "max_text_chars": 10_000,
        "timeout_seconds": 10,
        "memory_mb": 512,
    }
    config.update(values)
    return BoundedPdfExtractor(**config)


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_extractor_reads_bounded_page_metadata_in_child_process():
    result = extractor().extract(blank_pdf())

    assert result.ok is True
    assert result.page_count == 1
    assert result.pages_extracted == 1
    assert result.text == ""
    assert result.extractor_version is not None
    assert result.extractor_version.startswith("pypdf/")


def test_pdf_extractor_rejects_invalid_signature_before_process_start():
    result = extractor().extract(b"not a pdf")

    assert result.error_code == "PDF_SIGNATURE_INVALID"
    assert result.page_count is None


def test_pdf_extractor_rejects_document_over_its_own_byte_limit():
    result = extractor(max_bytes=8).extract(b"%PDF-1.7\nmore than eight bytes")

    assert result.error_code == "PDF_TOO_LARGE"
