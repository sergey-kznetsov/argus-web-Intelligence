from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse


@dataclass(slots=True)
class StructuredDataExtraction:
    document_type: str
    payload: Any | None = None
    encoding: str = "utf-8"
    delimiter: str | None = None
    has_header: bool | None = None
    row_count: int | None = None
    rows_extracted: int | None = None
    column_count: int | None = None
    truncated: bool = False
    extractor_version: str = "stdlib/1"
    error_code: str | None = None
    error_message: str | None = None


class BoundedStructuredDataExtractor:
    """Bounded parser for public JSON and delimited-text documents.

    The parser never performs network access and only returns source-declared data.
    Input is already transport-bounded, but parser-specific limits keep normalized
    output from turning one public dataset into an unbounded collection result.
    """

    _JSON_MEDIA_TYPES = {
        "application/json",
        "text/json",
        "application/geo+json",
    }
    _CSV_MEDIA_TYPES = {
        "text/csv",
        "application/csv",
        "application/vnd.ms-excel",
    }
    _TSV_MEDIA_TYPES = {
        "text/tab-separated-values",
        "text/tsv",
        "application/tsv",
    }
    _DELIMITED_FALLBACK_ENCODINGS = ("utf-8-sig", "cp1251")

    def __init__(
        self,
        *,
        max_bytes: int = 5 * 1024 * 1024,
        max_records: int = 1000,
        max_columns: int = 100,
        max_cell_chars: int = 10_000,
        max_json_depth: int = 32,
        max_json_nodes: int = 20_000,
    ) -> None:
        self.max_bytes = max_bytes
        self.max_records = max_records
        self.max_columns = max_columns
        self.max_cell_chars = max_cell_chars
        self.max_json_depth = max_json_depth
        self.max_json_nodes = max_json_nodes

    def detect(self, content_type: str | None, url: str, body: bytes | None = None) -> str | None:
        media_type = self._media_type(content_type)
        if media_type in self._CSV_MEDIA_TYPES:
            return "csv"
        if media_type in self._TSV_MEDIA_TYPES:
            return "tsv"
        if media_type in self._JSON_MEDIA_TYPES or media_type.endswith("+json"):
            return "json"

        suffix = PurePosixPath(unquote(urlparse(url).path)).suffix.casefold()
        if suffix == ".csv":
            return "csv"
        if suffix in {".tsv", ".tab"}:
            return "tsv"
        if suffix in {".json", ".geojson"}:
            return "json"

        if media_type in {"text/plain", "application/octet-stream", ""} and body:
            sample = body[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
            if sample.startswith((b"{", b"[")):
                return "json"
        return None

    def extract(
        self,
        body: bytes,
        *,
        content_type: str | None,
        url: str,
    ) -> StructuredDataExtraction:
        document_type = self.detect(content_type, url, body) or "unknown"
        if len(body) > self.max_bytes:
            return StructuredDataExtraction(
                document_type=document_type,
                error_code="STRUCTURED_DATA_TOO_LARGE",
                error_message="Structured document exceeds configured parser byte limit",
            )
        if document_type == "json":
            return self._extract_json(body)
        if document_type in {"csv", "tsv"}:
            return self._extract_delimited(body, content_type, document_type)
        return StructuredDataExtraction(
            document_type=document_type,
            error_code="STRUCTURED_DATA_UNSUPPORTED",
            error_message="Document is not a supported structured data format",
        )

    def _extract_json(self, body: bytes) -> StructuredDataExtraction:
        try:
            text = body.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError as exc:
            return StructuredDataExtraction(
                document_type="json",
                encoding="utf-8",
                error_code="STRUCTURED_DATA_DECODE_ERROR",
                error_message=f"JSON text is not valid UTF-8: {type(exc).__name__}",
            )
        try:
            payload = json.loads(text, parse_constant=self._reject_json_constant)
        except RecursionError:
            return StructuredDataExtraction(
                document_type="json",
                encoding="utf-8",
                error_code="STRUCTURED_DATA_LIMIT_EXCEEDED",
                error_message="JSON nesting exceeds the parser recursion limit",
            )
        except (json.JSONDecodeError, ValueError) as exc:
            return StructuredDataExtraction(
                document_type="json",
                encoding="utf-8",
                error_code="STRUCTURED_DATA_PARSE_ERROR",
                error_message=f"JSON document is invalid: {type(exc).__name__}",
            )

        within_limits, nodes = self._json_within_limits(payload)
        if not within_limits:
            return StructuredDataExtraction(
                document_type="json",
                encoding="utf-8",
                error_code="STRUCTURED_DATA_LIMIT_EXCEEDED",
                error_message=(
                    "JSON structure exceeds configured depth, node, string, or container limits"
                ),
            )

        row_count = len(payload) if isinstance(payload, list) else None
        column_count = (
            max((len(item) for item in payload if isinstance(item, dict)), default=0)
            if isinstance(payload, list)
            else len(payload)
            if isinstance(payload, dict)
            else None
        )
        return StructuredDataExtraction(
            document_type="json",
            payload=payload,
            encoding="utf-8",
            row_count=row_count,
            rows_extracted=row_count,
            column_count=column_count,
            extractor_version=f"stdlib-json/1;nodes={nodes}",
        )

    def _extract_delimited(
        self,
        body: bytes,
        content_type: str | None,
        document_type: str,
    ) -> StructuredDataExtraction:
        decoded = self._decode_delimited(body, content_type)
        if decoded is None:
            return StructuredDataExtraction(
                document_type=document_type,
                error_code="STRUCTURED_DATA_DECODE_ERROR",
                error_message="Delimited text could not be decoded with supported encodings",
            )
        text, encoding = decoded

        delimiter = "\t" if document_type == "tsv" else self._csv_delimiter(text)
        try:
            reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
            first_row = next(reader, None)
            if first_row is None:
                return StructuredDataExtraction(
                    document_type=document_type,
                    payload={"columns": [], "records": []},
                    encoding=encoding,
                    delimiter=delimiter,
                    has_header=False,
                    row_count=0,
                    rows_extracted=0,
                    column_count=0,
                )

            has_header = self._has_header(text, delimiter)
            rows: list[list[str]] = []
            truncated = False
            if not has_header:
                rows.append(first_row)
            header = first_row if has_header else []

            for row in reader:
                if len(rows) >= self.max_records:
                    truncated = True
                    break
                rows.append(row)

            sanitized_rows, row_truncated, column_count = self._bounded_rows(rows)
            truncated = truncated or row_truncated
            if has_header:
                bounded_header, header_truncated = self._bounded_row(header)
                truncated = truncated or header_truncated
                columns = self._unique_columns(bounded_header)
                records = [
                    {
                        columns[index]: row[index] if index < len(row) else ""
                        for index in range(len(columns))
                    }
                    for row in sanitized_rows
                ]
                payload: dict[str, Any] = {"columns": columns, "records": records}
                column_count = max(column_count, len(columns))
            else:
                payload = {"columns": [], "rows": sanitized_rows}

            return StructuredDataExtraction(
                document_type=document_type,
                payload=payload,
                encoding=encoding,
                delimiter=delimiter,
                has_header=has_header,
                row_count=len(sanitized_rows),
                rows_extracted=len(sanitized_rows),
                column_count=column_count,
                truncated=truncated,
                extractor_version="stdlib-csv/1",
            )
        except csv.Error as exc:
            return StructuredDataExtraction(
                document_type=document_type,
                encoding=encoding,
                delimiter=delimiter,
                error_code="STRUCTURED_DATA_PARSE_ERROR",
                error_message=f"Delimited document is invalid: {type(exc).__name__}",
            )

    @classmethod
    def _decode_delimited(
        cls,
        body: bytes,
        content_type: str | None,
    ) -> tuple[str, str] | None:
        candidates: list[str] = []
        bom_encoding = cls._bom_encoding(body)
        if bom_encoding:
            candidates.append(bom_encoding)
        declared = cls._declared_charset(content_type)
        if declared and declared not in candidates:
            candidates.append(declared)
        for encoding in cls._DELIMITED_FALLBACK_ENCODINGS:
            if encoding not in candidates:
                candidates.append(encoding)

        for encoding in candidates:
            try:
                return body.decode(encoding, errors="strict"), encoding
            except (LookupError, UnicodeDecodeError):
                continue
        return None

    @staticmethod
    def _bom_encoding(body: bytes) -> str | None:
        if body.startswith(b"\x00\x00\xfe\xff") or body.startswith(b"\xff\xfe\x00\x00"):
            return "utf-32"
        if body.startswith(b"\xfe\xff") or body.startswith(b"\xff\xfe"):
            return "utf-16"
        if body.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        return None

    def _bounded_rows(self, rows: list[list[str]]) -> tuple[list[list[str]], bool, int]:
        bounded: list[list[str]] = []
        truncated = False
        column_count = 0
        for row in rows[: self.max_records]:
            limited, row_truncated = self._bounded_row(row)
            bounded.append(limited)
            truncated = truncated or row_truncated
            column_count = max(column_count, len(limited))
        if len(rows) > self.max_records:
            truncated = True
        return bounded, truncated, column_count

    def _bounded_row(self, row: list[str]) -> tuple[list[str], bool]:
        truncated = len(row) > self.max_columns
        limited: list[str] = []
        for value in row[: self.max_columns]:
            if len(value) > self.max_cell_chars:
                truncated = True
                value = value[: self.max_cell_chars]
            limited.append(value)
        return limited, truncated

    def _json_within_limits(self, root: Any) -> tuple[bool, int]:
        stack: list[tuple[Any, int]] = [(root, 0)]
        nodes = 0
        while stack:
            value, depth = stack.pop()
            nodes += 1
            if nodes > self.max_json_nodes or depth > self.max_json_depth:
                return False, nodes
            if isinstance(value, str):
                if len(value) > self.max_cell_chars:
                    return False, nodes
                continue
            if isinstance(value, dict):
                if len(value) > self.max_columns:
                    return False, nodes
                for key, item in value.items():
                    if len(str(key)) > self.max_cell_chars:
                        return False, nodes
                    stack.append((item, depth + 1))
                continue
            if isinstance(value, list):
                if len(value) > self.max_records:
                    return False, nodes
                for item in value:
                    stack.append((item, depth + 1))
        return True, nodes

    @staticmethod
    def _reject_json_constant(value: str) -> None:
        raise ValueError(f"non-standard JSON constant: {value}")

    @staticmethod
    def _media_type(content_type: str | None) -> str:
        return str(content_type or "").split(";", 1)[0].strip().casefold()

    @staticmethod
    def _declared_charset(content_type: str | None) -> str | None:
        if content_type:
            for part in content_type.split(";")[1:]:
                key, separator, value = part.strip().partition("=")
                if separator and key.casefold() == "charset" and value.strip():
                    return value.strip().strip('"\'').casefold()
        return None

    @staticmethod
    def _csv_delimiter(text: str) -> str:
        sample = text[:16_384]
        try:
            return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
        except csv.Error:
            return ","

    @staticmethod
    def _has_header(text: str, delimiter: str) -> bool:
        sample = text[:16_384]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=delimiter)
            return csv.Sniffer().has_header(sample) if dialect else False
        except csv.Error:
            return False

    @staticmethod
    def _unique_columns(header: list[str]) -> list[str]:
        columns: list[str] = []
        counts: dict[str, int] = {}
        for index, raw in enumerate(header, start=1):
            base = raw.strip() or f"column_{index}"
            count = counts.get(base, 0) + 1
            counts[base] = count
            columns.append(base if count == 1 else f"{base}_{count}")
        return columns
