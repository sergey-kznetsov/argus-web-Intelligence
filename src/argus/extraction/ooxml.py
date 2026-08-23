from __future__ import annotations

import posixpath
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath
from urllib.parse import urlsplit
from xml.etree.ElementTree import Element, ParseError

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from argus.extraction.structured_data import StructuredDataExtraction


@dataclass(slots=True)
class _OoxmlError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class BoundedOoxmlExtractor:
    """Read bounded DOCX/XLSX packages entirely in memory.

    The extractor never writes ZIP members to disk, never follows package relationships
    outside the archive and parses XML only through defusedxml. Values are preserved as
    source-declared strings; formulas are recorded but never evaluated.
    """

    _ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}

    def __init__(
        self,
        *,
        max_bytes: int = 5 * 1024 * 1024,
        max_members: int = 1000,
        max_uncompressed_bytes: int = 20 * 1024 * 1024,
        max_member_bytes: int = 10 * 1024 * 1024,
        max_xml_nodes: int = 100_000,
        max_xml_depth: int = 64,
        max_records: int = 1000,
        max_columns: int = 100,
        max_cell_chars: int = 10_000,
        max_sheets: int = 50,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_members = max_members
        self.max_uncompressed_bytes = max_uncompressed_bytes
        self.max_member_bytes = max_member_bytes
        self.max_xml_nodes = max_xml_nodes
        self.max_xml_depth = max_xml_depth
        self.max_records = max_records
        self.max_columns = max_columns
        self.max_cell_chars = max_cell_chars
        self.max_sheets = max_sheets

    def extract(self, body: bytes, *, document_type: str) -> StructuredDataExtraction:
        if document_type not in {"docx", "xlsx"}:
            return self._error(
                document_type,
                "OOXML_FORMAT_UNSUPPORTED",
                "Only DOCX and XLSX packages are supported by the OOXML extractor",
            )
        if len(body) > self.max_bytes:
            return self._error(
                document_type,
                "OOXML_PACKAGE_TOO_LARGE",
                "OOXML package exceeds the configured compressed byte limit",
            )

        try:
            with zipfile.ZipFile(BytesIO(body), mode="r") as package:
                members, declared_uncompressed = self._preflight(package)
                read_budget = [self.max_uncompressed_bytes]
                xml_node_budget = [self.max_xml_nodes]
                self._parse_xml(
                    self._read_member(package, members, "[Content_Types].xml", read_budget),
                    xml_node_budget,
                )
                if document_type == "docx":
                    result = self._extract_docx(package, members, read_budget, xml_node_budget)
                else:
                    result = self._extract_xlsx(package, members, read_budget, xml_node_budget)
                result.extractor_version = (
                    f"ooxml-stdlib/1;members={len(members)};"
                    f"declared_uncompressed={declared_uncompressed}"
                )
                return result
        except _OoxmlError as exc:
            return self._error(document_type, exc.code, exc.message)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, RuntimeError, ValueError) as exc:
            return self._error(
                document_type,
                "OOXML_PACKAGE_INVALID",
                f"OOXML package is invalid: {type(exc).__name__}",
            )

    def _preflight(self, package: zipfile.ZipFile) -> tuple[dict[str, zipfile.ZipInfo], int]:
        infos = [item for item in package.infolist() if not item.is_dir()]
        if not infos:
            raise _OoxmlError("OOXML_PACKAGE_EMPTY", "OOXML package contains no file parts")
        if len(infos) > self.max_members:
            raise _OoxmlError(
                "OOXML_MEMBER_LIMIT_EXCEEDED",
                "OOXML package contains more members than the configured limit",
            )

        members: dict[str, zipfile.ZipInfo] = {}
        casefolded: set[str] = set()
        declared_total = 0
        for info in infos:
            name = self._safe_member_name(info.filename)
            folded = name.casefold()
            if name in members or folded in casefolded:
                raise _OoxmlError(
                    "OOXML_DUPLICATE_MEMBER",
                    "OOXML package contains duplicate or case-colliding member names",
                )
            if info.flag_bits & 0x1:
                raise _OoxmlError(
                    "OOXML_ENCRYPTED_MEMBER",
                    "Encrypted OOXML package members are not supported",
                )
            if info.compress_type not in self._ALLOWED_COMPRESSION:
                raise _OoxmlError(
                    "OOXML_COMPRESSION_UNSUPPORTED",
                    "OOXML member uses an unsupported ZIP compression method",
                )
            if info.file_size < 0 or info.file_size > self.max_member_bytes:
                raise _OoxmlError(
                    "OOXML_MEMBER_TOO_LARGE",
                    "OOXML member exceeds the configured uncompressed member limit",
                )
            declared_total += info.file_size
            if declared_total > self.max_uncompressed_bytes:
                raise _OoxmlError(
                    "OOXML_UNCOMPRESSED_LIMIT_EXCEEDED",
                    "OOXML package exceeds the configured total uncompressed byte limit",
                )
            members[name] = info
            casefolded.add(folded)

        if "[Content_Types].xml" not in members:
            raise _OoxmlError(
                "OOXML_CONTENT_TYPES_MISSING",
                "OOXML package does not contain [Content_Types].xml",
            )
        return members, declared_total

    def _extract_docx(
        self,
        package: zipfile.ZipFile,
        members: dict[str, zipfile.ZipInfo],
        read_budget: list[int],
        xml_node_budget: list[int],
    ) -> StructuredDataExtraction:
        part = "word/document.xml"
        if part not in members:
            raise _OoxmlError(
                "OOXML_MAIN_PART_MISSING",
                "DOCX package does not contain word/document.xml",
            )
        root = self._parse_xml(
            self._read_member(package, members, part, read_budget),
            xml_node_budget,
        )
        body = self._first_descendant(root, "body")
        if body is None:
            raise _OoxmlError("OOXML_DOCUMENT_BODY_MISSING", "DOCX main part has no body")

        blocks: list[dict[str, object]] = []
        records_used = 0
        max_columns_seen = 0
        truncated = False
        for child in list(body):
            kind = self._local_name(child)
            if kind == "p":
                if records_used >= self.max_records:
                    truncated = True
                    break
                text, text_truncated = self._word_text(child)
                blocks.append({"type": "paragraph", "text": text})
                records_used += 1
                truncated = truncated or text_truncated
            elif kind == "tbl":
                if records_used >= self.max_records:
                    truncated = True
                    break
                table, consumed, columns, table_truncated = self._word_table(
                    child,
                    self.max_records - records_used,
                )
                blocks.append({"type": "table", "rows": table})
                records_used += consumed
                max_columns_seen = max(max_columns_seen, columns)
                truncated = truncated or table_truncated

        return StructuredDataExtraction(
            document_type="docx",
            entity_type="document",
            payload={"blocks": blocks},
            encoding="utf-8/xml",
            row_count=records_used,
            rows_extracted=records_used,
            column_count=max_columns_seen or None,
            truncated=truncated,
            extractor_version="ooxml-stdlib/1",
        )

    def _extract_xlsx(
        self,
        package: zipfile.ZipFile,
        members: dict[str, zipfile.ZipInfo],
        read_budget: list[int],
        xml_node_budget: list[int],
    ) -> StructuredDataExtraction:
        workbook_part = "xl/workbook.xml"
        rels_part = "xl/_rels/workbook.xml.rels"
        if workbook_part not in members or rels_part not in members:
            raise _OoxmlError(
                "OOXML_MAIN_PART_MISSING",
                "XLSX package is missing workbook.xml or workbook relationships",
            )
        workbook = self._parse_xml(
            self._read_member(package, members, workbook_part, read_budget),
            xml_node_budget,
        )
        relationships = self._parse_relationships(
            self._parse_xml(
                self._read_member(package, members, rels_part, read_budget),
                xml_node_budget,
            ),
            workbook_part,
        )
        shared_strings, shared_truncated = self._shared_strings(
            package,
            members,
            read_budget,
            xml_node_budget,
        )

        sheets: list[dict[str, object]] = []
        records_used = 0
        max_columns_seen = 0
        truncated = shared_truncated
        sheet_elements = [
            item for item in workbook.iter() if self._local_name(item) == "sheet"
        ]
        if len(sheet_elements) > self.max_sheets:
            truncated = True
            sheet_elements = sheet_elements[: self.max_sheets]

        for sheet in sheet_elements:
            if records_used >= self.max_records:
                truncated = True
                break
            rel_id = self._attribute(sheet, "id")
            name = self._attribute(sheet, "name") or f"Sheet {len(sheets) + 1}"
            target = relationships.get(rel_id or "")
            if not target:
                raise _OoxmlError(
                    "OOXML_RELATIONSHIP_MISSING",
                    "XLSX worksheet relationship is missing or external",
                )
            if target not in members:
                raise _OoxmlError(
                    "OOXML_PART_MISSING",
                    "XLSX worksheet relationship points to a missing package part",
                )
            worksheet = self._parse_xml(
                self._read_member(package, members, target, read_budget),
                xml_node_budget,
            )
            rows, consumed, columns, sheet_truncated = self._worksheet_rows(
                worksheet,
                shared_strings,
                self.max_records - records_used,
            )
            sheets.append({"name": self._bounded_text(name)[0], "rows": rows})
            records_used += consumed
            max_columns_seen = max(max_columns_seen, columns)
            truncated = truncated or sheet_truncated

        return StructuredDataExtraction(
            document_type="xlsx",
            entity_type="dataset",
            payload={"sheets": sheets},
            encoding="utf-8/xml",
            row_count=records_used,
            rows_extracted=records_used,
            column_count=max_columns_seen or None,
            truncated=truncated,
            extractor_version="ooxml-stdlib/1",
        )

    def _shared_strings(
        self,
        package: zipfile.ZipFile,
        members: dict[str, zipfile.ZipInfo],
        read_budget: list[int],
        xml_node_budget: list[int],
    ) -> tuple[list[str], bool]:
        part = "xl/sharedStrings.xml"
        if part not in members:
            return [], False
        root = self._parse_xml(
            self._read_member(package, members, part, read_budget),
            xml_node_budget,
        )
        limit = self.max_records * self.max_columns
        values: list[str] = []
        truncated = False
        for item in root.iter():
            if self._local_name(item) != "si":
                continue
            if len(values) >= limit:
                truncated = True
                break
            text = "".join(
                node.text or "" for node in item.iter() if self._local_name(node) == "t"
            )
            text, text_truncated = self._bounded_text(text)
            values.append(text)
            truncated = truncated or text_truncated
        return values, truncated

    def _worksheet_rows(
        self,
        worksheet: Element,
        shared_strings: list[str],
        record_budget: int,
    ) -> tuple[list[dict[str, object]], int, int, bool]:
        rows: list[dict[str, object]] = []
        max_columns_seen = 0
        truncated = False
        for row in worksheet.iter():
            if self._local_name(row) != "row":
                continue
            if len(rows) >= record_budget:
                truncated = True
                break
            cells: list[dict[str, object]] = []
            cell_elements = [item for item in list(row) if self._local_name(item) == "c"]
            if len(cell_elements) > self.max_columns:
                truncated = True
                cell_elements = cell_elements[: self.max_columns]
            for cell in cell_elements:
                parsed, cell_truncated = self._worksheet_cell(cell, shared_strings)
                cells.append(parsed)
                truncated = truncated or cell_truncated
            max_columns_seen = max(max_columns_seen, len(cells))
            row_payload: dict[str, object] = {"cells": cells}
            row_number = self._attribute(row, "r")
            if row_number:
                row_payload["row"] = row_number
            rows.append(row_payload)
        return rows, len(rows), max_columns_seen, truncated

    def _worksheet_cell(
        self,
        cell: Element,
        shared_strings: list[str],
    ) -> tuple[dict[str, object], bool]:
        cell_type = (self._attribute(cell, "t") or "n").casefold()
        reference = self._attribute(cell, "r")
        raw_value = self._child_text(cell, "v")
        formula = self._child_text(cell, "f")
        truncated = False
        payload: dict[str, object] = {}
        if reference:
            payload["ref"] = reference

        if cell_type == "s":
            payload["type"] = "shared_string"
            try:
                index = int(raw_value or "")
            except ValueError:
                index = -1
            payload["shared_string_index"] = index
            if 0 <= index < len(shared_strings):
                payload["value"] = shared_strings[index]
        elif cell_type == "inlinestr":
            payload["type"] = "inline_string"
            text = "".join(
                node.text or "" for node in cell.iter() if self._local_name(node) == "t"
            )
            text, truncated = self._bounded_text(text)
            payload["value"] = text
        else:
            type_names = {
                "b": "boolean",
                "d": "date",
                "e": "error",
                "n": "number",
                "str": "string",
            }
            payload["type"] = type_names.get(cell_type, cell_type or "number")
            if raw_value is not None:
                value, value_truncated = self._bounded_text(raw_value)
                payload["value"] = value
                truncated = truncated or value_truncated
        if formula is not None:
            formula, formula_truncated = self._bounded_text(formula)
            payload["formula"] = formula
            truncated = truncated or formula_truncated
        return payload, truncated

    def _parse_relationships(self, root: Element, base_part: str) -> dict[str, str]:
        relationships: dict[str, str] = {}
        for item in root.iter():
            if self._local_name(item) != "relationship":
                continue
            rel_id = self._attribute(item, "id")
            target = self._attribute(item, "target")
            target_mode = (self._attribute(item, "targetmode") or "").casefold()
            rel_type = (self._attribute(item, "type") or "").casefold()
            if not rel_id or not target or target_mode == "external":
                continue
            if rel_type and not rel_type.endswith("/worksheet"):
                continue
            relationships[rel_id] = self._resolve_part(base_part, target)
        return relationships

    def _word_table(
        self,
        table: Element,
        record_budget: int,
    ) -> tuple[list[list[str]], int, int, bool]:
        rows: list[list[str]] = []
        max_columns_seen = 0
        truncated = False
        row_elements = [item for item in list(table) if self._local_name(item) == "tr"]
        for row in row_elements:
            if len(rows) >= record_budget:
                truncated = True
                break
            cells = [item for item in list(row) if self._local_name(item) == "tc"]
            if len(cells) > self.max_columns:
                cells = cells[: self.max_columns]
                truncated = True
            values: list[str] = []
            for cell in cells:
                text, text_truncated = self._word_text(cell)
                values.append(text)
                truncated = truncated or text_truncated
            rows.append(values)
            max_columns_seen = max(max_columns_seen, len(values))
        return rows, len(rows), max_columns_seen, truncated

    def _word_text(self, element: Element) -> tuple[str, bool]:
        chunks: list[str] = []
        for item in element.iter():
            name = self._local_name(item)
            if name == "t" and item.text:
                chunks.append(item.text)
            elif name == "tab":
                chunks.append("\t")
            elif name in {"br", "cr"}:
                chunks.append("\n")
        return self._bounded_text("".join(chunks))

    def _parse_xml(self, data: bytes, node_budget: list[int]) -> Element:
        depth = 0
        nodes = 0
        try:
            for event, element in DefusedET.iterparse(
                BytesIO(data),
                events=("start", "end"),
            ):
                if event == "start":
                    nodes += 1
                    if nodes > node_budget[0] or depth > self.max_xml_depth:
                        raise _OoxmlError(
                            "OOXML_XML_LIMIT_EXCEEDED",
                            "OOXML XML part exceeds configured node or depth limits",
                        )
                    depth += 1
                else:
                    depth = max(0, depth - 1)
                    element.clear()
            node_budget[0] -= nodes
            if node_budget[0] < 0:
                raise _OoxmlError(
                    "OOXML_XML_LIMIT_EXCEEDED",
                    "OOXML package exceeds the configured XML node budget",
                )
            return DefusedET.fromstring(data)
        except _OoxmlError:
            raise
        except (DefusedXmlException, ParseError, ValueError, RecursionError) as exc:
            raise _OoxmlError(
                "OOXML_XML_INVALID",
                f"OOXML XML part is invalid or unsafe: {type(exc).__name__}",
            ) from exc

    def _read_member(
        self,
        package: zipfile.ZipFile,
        members: dict[str, zipfile.ZipInfo],
        name: str,
        read_budget: list[int],
    ) -> bytes:
        info = members.get(name)
        if info is None:
            raise _OoxmlError("OOXML_PART_MISSING", f"OOXML package part is missing: {name}")
        limit = min(self.max_member_bytes, read_budget[0])
        if limit < 0:
            raise _OoxmlError(
                "OOXML_UNCOMPRESSED_LIMIT_EXCEEDED",
                "OOXML read budget is exhausted",
            )
        try:
            with package.open(info, mode="r") as stream:
                data = stream.read(limit + 1)
        except (RuntimeError, NotImplementedError, OSError, zipfile.BadZipFile) as exc:
            raise _OoxmlError(
                "OOXML_MEMBER_READ_FAILED",
                f"OOXML package member could not be read: {type(exc).__name__}",
            ) from exc
        if len(data) > limit:
            raise _OoxmlError(
                "OOXML_MEMBER_TOO_LARGE",
                "OOXML member exceeds the configured read limit",
            )
        read_budget[0] -= len(data)
        return data

    @staticmethod
    def _safe_member_name(raw: str) -> str:
        name = raw.replace("\\", "/")
        path = PurePosixPath(name)
        if not name or path.is_absolute() or ".." in path.parts or "\x00" in name:
            raise _OoxmlError(
                "OOXML_MEMBER_PATH_INVALID",
                "OOXML package contains an unsafe member path",
            )
        normalized = posixpath.normpath(name)
        if normalized.startswith("../") or normalized in {"", ".", ".."}:
            raise _OoxmlError(
                "OOXML_MEMBER_PATH_INVALID",
                "OOXML package contains an unsafe member path",
            )
        return normalized

    @staticmethod
    def _resolve_part(base_part: str, target: str) -> str:
        parsed = urlsplit(target)
        if parsed.scheme or parsed.netloc:
            raise _OoxmlError(
                "OOXML_EXTERNAL_RELATIONSHIP",
                "OOXML package relationship points outside the package",
            )
        if target.startswith("/"):
            candidate = target.lstrip("/")
        else:
            candidate = posixpath.join(posixpath.dirname(base_part), target)
        normalized = posixpath.normpath(candidate.replace("\\", "/"))
        if normalized.startswith("../") or normalized in {"", ".", ".."}:
            raise _OoxmlError(
                "OOXML_RELATIONSHIP_PATH_INVALID",
                "OOXML package relationship escapes the package namespace",
            )
        return normalized

    def _bounded_text(self, value: str) -> tuple[str, bool]:
        if len(value) <= self.max_cell_chars:
            return value, False
        return value[: self.max_cell_chars], True

    @staticmethod
    def _local_name(element: Element) -> str:
        return str(element.tag).split("}")[-1].casefold()

    @classmethod
    def _attribute(cls, element: Element, name: str) -> str | None:
        wanted = name.casefold()
        for key, value in element.attrib.items():
            if str(key).split("}")[-1].casefold() == wanted:
                return str(value)
        return None

    @classmethod
    def _child_text(cls, element: Element, name: str) -> str | None:
        wanted = name.casefold()
        for child in list(element):
            if cls._local_name(child) == wanted:
                return child.text or ""
        return None

    @classmethod
    def _first_descendant(cls, element: Element, name: str) -> Element | None:
        wanted = name.casefold()
        for item in element.iter():
            if cls._local_name(item) == wanted:
                return item
        return None

    @staticmethod
    def _error(document_type: str, code: str, message: str) -> StructuredDataExtraction:
        return StructuredDataExtraction(
            document_type=document_type,
            entity_type="document" if document_type == "docx" else "dataset",
            error_code=code,
            error_message=message,
            extractor_version="ooxml-stdlib/1",
        )
