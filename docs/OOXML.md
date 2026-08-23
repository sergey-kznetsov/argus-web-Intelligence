# Bounded OOXML document extraction

ARGUS supports deterministic, bounded extraction from public DOCX and XLSX files. The implementation follows the Office Open XML package model used by ISO/IEC 29500: an OOXML document is a ZIP package containing related XML parts.

This is factual source normalization only. ARGUS does not evaluate spreadsheet formulas, infer spreadsheet data types beyond the type declared by SpreadsheetML, execute macros, render Word layout or follow package relationships onto the network.

## Supported formats

Parsed:

```text
DOCX  WordprocessingML
XLSX  SpreadsheetML
```

File-level evidence only:

```text
DOC   legacy OLE/Word binary
XLS   legacy OLE/BIFF spreadsheet
```

Legacy DOC/XLS deliberately remain separate. They are not ZIP/XML formats and must not be routed through the OOXML parser or treated as CSV.

## Package safety boundary

OOXML is treated as an untrusted compressed archive. ARGUS never calls `ZipFile.extract()` or `extractall()` and never writes package members to disk.

Before reading document parts the package is preflighted. ARGUS rejects:

- more members than the configured package limit;
- duplicate or case-colliding member names;
- absolute paths, parent traversal and invalid member paths;
- encrypted ZIP members;
- ZIP compression methods outside stored/deflate;
- a member whose declared uncompressed size exceeds the member limit;
- a package whose declared total uncompressed size exceeds the package limit;
- a member that actually returns more bytes than the bounded read budget;
- packages without `[Content_Types].xml`.

The central-directory size check is not the only defense. Every member read is bounded independently so a misleading archive size declaration cannot turn an individual read into an unbounded allocation.

## XML safety

Every XML part read by the extractor passes through `defusedxml`.

Before the same bounded part is converted to an ElementTree, ARGUS performs a streaming `iterparse` pass that consumes a package-wide XML-node budget and enforces maximum XML depth. Unsafe entity payloads and invalid XML are rejected.

The OOXML parser does not perform XInclude, schema resolution, external entity resolution or any other parser-initiated network access.

## DOCX normalization

The main factual source is `word/document.xml`.

ARGUS currently preserves top-level body content as ordered blocks:

```json
{
  "blocks": [
    {"type": "paragraph", "text": "Example"},
    {
      "type": "table",
      "rows": [["A", "B"], ["C", "D"]]
    }
  ]
}
```

WordprocessingML text nodes are concatenated in document order. Tabs and line breaks are preserved as text control characters. Tables preserve row/cell structure. Styling, page layout, images, comments, tracked changes and embedded objects are not interpreted in this first parser version.

DOCX is exposed as `entity_type=document` and `source_kind=office_document`.

## XLSX normalization

ARGUS reads the workbook through the package relationships defined by SpreadsheetML:

```text
xl/workbook.xml
        |
        v
xl/_rels/workbook.xml.rels
        |
        v
xl/worksheets/*.xml
```

`xl/sharedStrings.xml` is used when present. A shared string is resolved only inside the package and is never dereferenced externally.

Normalized output keeps sheets, rows and cells:

```json
{
  "sheets": [
    {
      "name": "Data",
      "rows": [
        {
          "row": "1",
          "cells": [
            {"ref": "A1", "type": "shared_string", "value": "School"},
            {"ref": "B1", "type": "number", "value": "3"}
          ]
        }
      ]
    }
  ]
}
```

Numeric/date/boolean/error values remain source strings together with their declared SpreadsheetML type. ARGUS does not apply Excel cell styles to reinterpret serial numbers as dates.

If a cell contains a formula, the formula text and cached source value may both be preserved:

```json
{
  "ref": "C1",
  "type": "number",
  "value": "6",
  "formula": "B1*2"
}
```

The formula is never evaluated by ARGUS.

XLSX is exposed as `entity_type=dataset` and `source_kind=office_spreadsheet`.

## Relationship policy

Package relationships are resolved only inside the ZIP namespace. URL schemes and network locations are rejected for required worksheet relationships. `TargetMode=External` is never followed.

A relationship path may normalize within the package root, but it cannot escape above that root.

## Limits

OOXML currently derives its operational limits from the existing structured-data settings instead of adding a second configuration surface.

With default structured-data values:

```text
compressed package                    <= 5 MiB
actual/declared uncompressed package <= 20 MiB
single uncompressed member           <= 10 MiB
package members                      <= 1000
XML node budget                      = structured JSON node limit
XML depth                            = structured JSON depth limit
records/rows                         = structured record limit
columns/cells per row                = structured column limit
cell/paragraph text                  = structured cell-character limit
sheets                               <= min(structured column limit, 50)
```

If the structured-data byte limit is configured below its default, derived OOXML package/member budgets decrease with it. The hard caps of 20 MiB total and 10 MiB per member prevent an increased compressed transport limit from creating proportionally unbounded decompression.

## Partial and error behavior

A valid DOCX/XLSX that reaches record, sheet, column or text limits returns bounded data with `partial=true` and `OOXML_EXTRACTION_TRUNCATED`.

Unsafe or malformed packages return file-backed partial evidence with a specific error such as:

```text
OOXML_PACKAGE_TOO_LARGE
OOXML_PACKAGE_INVALID
OOXML_MEMBER_LIMIT_EXCEEDED
OOXML_UNCOMPRESSED_LIMIT_EXCEEDED
OOXML_MEMBER_TOO_LARGE
OOXML_MEMBER_PATH_INVALID
OOXML_DUPLICATE_MEMBER
OOXML_ENCRYPTED_MEMBER
OOXML_COMPRESSION_UNSUPPORTED
OOXML_CONTENT_TYPES_MISSING
OOXML_MAIN_PART_MISSING
OOXML_PART_MISSING
OOXML_EXTERNAL_RELATIONSHIP
OOXML_RELATIONSHIP_PATH_INVALID
OOXML_RELATIONSHIP_MISSING
OOXML_XML_LIMIT_EXCEEDED
OOXML_XML_INVALID
```

The original HTTP response SHA-256 remains the document identity even when content extraction fails.
