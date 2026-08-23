import zipfile
from io import BytesIO

from argus.extraction.ooxml import BoundedOoxmlExtractor


CONTENT_TYPES = b"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>
"""


def package(parts: dict[str, bytes], *, compression=zipfile.ZIP_DEFLATED) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        archive.writestr("[Content_Types].xml", CONTENT_TYPES)
        for name, body in parts.items():
            archive.writestr(name, body)
    return buffer.getvalue()


def docx(document_xml: bytes) -> bytes:
    return package({"word/document.xml": document_xml})


def xlsx(*, sheet_xml: bytes, shared_strings: bytes | None = None, rel_target=None) -> bytes:
    target = rel_target or "worksheets/sheet1.xml"
    workbook = b"""<?xml version="1.0" encoding="UTF-8"?>
    <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
      xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
      <sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets>
    </workbook>
    """
    relationships = f"""<?xml version="1.0" encoding="UTF-8"?>
    <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
      <Relationship Id="rId1"
        Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
        Target="{target}"/>
    </Relationships>
    """.encode()
    parts = {
        "xl/workbook.xml": workbook,
        "xl/_rels/workbook.xml.rels": relationships,
        "xl/worksheets/sheet1.xml": sheet_xml,
    }
    if shared_strings is not None:
        parts["xl/sharedStrings.xml"] = shared_strings
    return package(parts)


def extractor(**overrides) -> BoundedOoxmlExtractor:
    values = {
        "max_bytes": 1024 * 1024,
        "max_members": 30,
        "max_uncompressed_bytes": 1024 * 1024,
        "max_member_bytes": 512 * 1024,
        "max_xml_nodes": 1000,
        "max_xml_depth": 32,
        "max_records": 10,
        "max_columns": 10,
        "max_cell_chars": 100,
        "max_sheets": 5,
    }
    values.update(overrides)
    return BoundedOoxmlExtractor(**values)


def test_docx_extracts_top_level_paragraphs_and_tables_without_format_inference():
    document = b"""<?xml version="1.0" encoding="UTF-8"?>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body>
        <w:p><w:r><w:t>Первый абзац</w:t></w:r></w:p>
        <w:tbl>
          <w:tr>
            <w:tc><w:p><w:r><w:t>Ячейка 1</w:t></w:r></w:p></w:tc>
            <w:tc><w:p><w:r><w:t>Ячейка 2</w:t></w:r></w:p></w:tc>
          </w:tr>
        </w:tbl>
      </w:body>
    </w:document>
    """

    result = extractor().extract(docx(document), document_type="docx")

    assert result.error_code is None
    assert result.document_type == "docx"
    assert result.truncated is False
    assert result.payload == {
        "blocks": [
            {"type": "paragraph", "text": "Первый абзац"},
            {"type": "table", "rows": [["Ячейка 1", "Ячейка 2"]]},
        ]
    }
    assert result.row_count == 2
    assert result.column_count == 2
    assert result.extractor_version.startswith("ooxml-stdlib/1;members=")


def test_xlsx_extracts_shared_strings_raw_numbers_and_formula_without_evaluation():
    shared = b"""<?xml version="1.0" encoding="UTF-8"?>
    <sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <si><t>Школа</t></si>
    </sst>
    """
    sheet = b"""<?xml version="1.0" encoding="UTF-8"?>
    <worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData>
        <row r="1">
          <c r="A1" t="s"><v>0</v></c>
          <c r="B1"><v>3</v></c>
          <c r="C1"><f>B1*2</f><v>6</v></c>
        </row>
      </sheetData>
    </worksheet>
    """

    result = extractor().extract(
        xlsx(sheet_xml=sheet, shared_strings=shared),
        document_type="xlsx",
    )

    assert result.error_code is None
    assert result.document_type == "xlsx"
    assert result.row_count == 1
    cells = result.payload["sheets"][0]["rows"][0]["cells"]
    assert cells[0] == {
        "ref": "A1",
        "type": "shared_string",
        "shared_string_index": 0,
        "value": "Школа",
    }
    assert cells[1] == {"ref": "B1", "type": "number", "value": "3"}
    assert cells[2] == {
        "ref": "C1",
        "type": "number",
        "value": "6",
        "formula": "B1*2",
    }


def test_ooxml_rejects_unsafe_xml_entity():
    document = b"""<!DOCTYPE root [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:p><w:r><w:t>&xxe;</w:t></w:r></w:p></w:body>
    </w:document>
    """

    result = extractor().extract(docx(document), document_type="docx")

    assert result.payload is None
    assert result.error_code == "OOXML_XML_INVALID"


def test_ooxml_rejects_member_path_traversal_before_reading_parts():
    malicious = package(
        {
            "../word/document.xml": b"<document/>",
            "word/document.xml": b"<document/>",
        }
    )

    result = extractor().extract(malicious, document_type="docx")

    assert result.payload is None
    assert result.error_code == "OOXML_MEMBER_PATH_INVALID"


def test_ooxml_rejects_declared_uncompressed_package_over_budget():
    oversized = package(
        {
            "word/document.xml": (
                b'<w:document xmlns:w="http://schemas.openxmlformats.org/'
                b'wordprocessingml/2006/main"><w:body/>'
                + (b" " * 5000)
                + b"</w:document>"
            )
        }
    )

    result = extractor(
        max_uncompressed_bytes=1024,
        max_member_bytes=10_000,
    ).extract(oversized, document_type="docx")

    assert result.payload is None
    assert result.error_code == "OOXML_UNCOMPRESSED_LIMIT_EXCEEDED"


def test_xlsx_external_worksheet_relationship_is_not_followed():
    sheet = b"""<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData/>
    </worksheet>"""
    body = xlsx(sheet_xml=sheet, rel_target="https://example.com/sheet.xml")

    result = extractor().extract(body, document_type="xlsx")

    assert result.payload is None
    assert result.error_code == "OOXML_EXTERNAL_RELATIONSHIP"


def test_docx_empty_tables_still_consume_record_budget():
    document = b"""<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
      <w:body><w:tbl/><w:tbl/><w:tbl/></w:body>
    </w:document>"""

    result = extractor(max_records=2).extract(docx(document), document_type="docx")

    assert result.error_code is None
    assert result.truncated is True
    assert len(result.payload["blocks"]) == 2
