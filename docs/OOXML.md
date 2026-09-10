# Ограниченное извлечение OOXML

ARGUS поддерживает детерминированное bounded извлечение из публичных DOCX и XLSX. Реализация следует package model Office Open XML (ISO/IEC 29500): OOXML document — ZIP package со связанными XML parts.

Это factual normalization. ARGUS не исполняет spreadsheet formulas, не рендерит Word layout, не запускает macros и не следует package relationships в сеть.

## Форматы

Разбираются:

```text
DOCX  WordprocessingML
XLSX  SpreadsheetML
```

Только file-level Evidence:

```text
DOC   legacy OLE/Word binary
XLS   legacy OLE/BIFF spreadsheet
```

DOC/XLS не направляются в OOXML parser и не трактуются как CSV.

## Package security

OOXML считается недоверенным compressed archive. `ZipFile.extract()`/`extractall()` не используются, members на диск не пишутся.

Preflight отклоняет:

- слишком много members;
- duplicate/case-colliding names;
- absolute/parent-traversal/invalid paths;
- encrypted members;
- unsupported ZIP compression;
- declared или фактический member size сверх лимита;
- total uncompressed package size сверх лимита;
- package без `[Content_Types].xml`.

Каждый member read ограничивается независимо, поэтому ложный central-directory size не даёт unbounded allocation.

## XML security

Каждый XML part проходит `defusedxml` и streaming `iterparse` preflight с package-wide node budget и max depth. External entities, XInclude, schemas и parser network access отсутствуют.

## DOCX normalization

Основной source — `word/document.xml`. Top-level body сохраняется ordered blocks:

```json
{
  "blocks": [
    {"type": "paragraph", "text": "Пример"},
    {"type": "table", "rows": [["A", "B"], ["C", "D"]]}
  ]
}
```

WordprocessingML text nodes объединяются в document order. Tabs/line breaks сохраняются. Tables сохраняют row/cell structure. Styling, page layout, images, comments, tracked changes и embedded objects не интерпретируются текущим parser.

DOCX: `entity_type=document`, `source_kind=office_document`.

## XLSX normalization

Workbook читается через package relationships:

```text
xl/workbook.xml
  -> xl/_rels/workbook.xml.rels
  -> xl/worksheets/*.xml
```

`xl/sharedStrings.xml` используется внутри package. Внешних dereference нет.

Normalized output сохраняет sheets/rows/cells. Numeric/date/boolean/error values остаются source strings вместе с SpreadsheetML type; ARGUS не применяет Excel styles для превращения serial number в date.

Formula text и cached source value могут сохраняться вместе, но formula никогда не вычисляется ARGUS.

XLSX: `entity_type=dataset`, `source_kind=office_spreadsheet`.

## Relationship policy

Relationships разрешаются только внутри ZIP namespace. Network locations и `TargetMode=External` не follow'ятся. Path не может выйти выше package root.

## Limits

Operational limits выводятся из structured-data settings. При defaults:

```text
compressed package <= 5 MiB
uncompressed package <= 20 MiB
single member <= 10 MiB
members <= 1000
XML nodes/depth = structured limits
rows = record limit
cells/row = column limit
text = cell-char limit
sheets <= min(column limit, 50)
```

При уменьшении structured byte limit производные OOXML limits тоже уменьшаются. Hard caps 20 MiB/10 MiB не дают бесконтрольно расширить decompression при увеличенном transport limit.

## Partial/error behavior

Valid DOCX/XLSX, достигший sheet/record/column/text limits, возвращает bounded data, `partial=true`, `OOXML_EXTRACTION_TRUNCATED`.

Unsafe/malformed package возвращает file-backed partial Evidence и конкретный error code, например:

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

SHA-256 исходного HTTP response остаётся document identity даже при failure extraction.
