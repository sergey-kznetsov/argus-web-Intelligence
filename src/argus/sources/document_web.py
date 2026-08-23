from __future__ import annotations

import asyncio
import hashlib
import json
from urllib.parse import unquote, urlparse

from argus.contracts.models import (
    CollectionRequest,
    Evidence,
    EvidenceSource,
    Observation,
    StructuredError,
)
from argus.extraction.pdf import BoundedPdfExtractor, PdfExtraction
from argus.extraction.structured_data import (
    BoundedStructuredDataExtractor,
    StructuredDataExtraction,
)
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.sources.base import SourceResult, SourceTask
from argus.sources.generic_web import GenericWebAdapter


class DocumentAwareGenericWebAdapter(GenericWebAdapter):
    """Generic public-web source with bounded local extraction for public documents."""

    def __init__(
        self,
        *args,
        pdf_extractor: BoundedPdfExtractor,
        structured_data_extractor: BoundedStructuredDataExtractor | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.pdf_extractor = pdf_extractor
        self.structured_data_extractor = (
            structured_data_extractor or BoundedStructuredDataExtractor()
        )

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        if fetched.blocked:
            return await super().extract(task, fetched, request)
        if self._is_pdf(fetched):
            body = fetched.body
            if body is None:
                return SourceResult(
                    observations=[],
                    errors=[
                        StructuredError(
                            code="PDF_BINARY_UNAVAILABLE",
                            message="PDF response did not retain its bounded binary body",
                            retryable=True,
                            source_id=self.source_id,
                        )
                    ],
                )
            extraction = await asyncio.to_thread(self.pdf_extractor.extract, body)
            return await self._pdf_result(task, fetched, request, body, extraction)

        structured_type = self._structured_type(fetched)
        if structured_type is not None:
            body = fetched.body
            if body is None:
                return SourceResult(
                    observations=[],
                    errors=[
                        StructuredError(
                            code="STRUCTURED_DATA_BINARY_UNAVAILABLE",
                            message=(
                                "Structured response did not retain its bounded source body"
                            ),
                            retryable=True,
                            source_id=self.source_id,
                        )
                    ],
                )
            assert self.structured_data_extractor is not None
            extraction = await asyncio.to_thread(
                self.structured_data_extractor.extract,
                body,
                content_type=fetched.content_type,
                url=fetched.final_url,
            )
            return await self._structured_result(
                task,
                fetched,
                request,
                body,
                extraction,
            )
        return await super().extract(task, fetched, request)

    async def _pdf_result(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
        body: bytes,
        extraction: PdfExtraction,
    ) -> SourceResult:
        collection_id = str(task.metadata.get("collection_id", ""))
        research_goals = self._research_goals(task)
        binary_hash = hashlib.sha256(body).hexdigest()
        snapshot_content = self._snapshot_content(binary_hash, extraction)
        snapshot = await self.snapshots.capture(
            self.source_id,
            fetched.final_url,
            snapshot_content,
            "text/plain; charset=utf-8",
            collection_id=collection_id,
        )

        observation_id = stable_observation_id(
            collection_id=collection_id,
            source_id=self.source_id,
            entity_type="document",
            source_url=fetched.final_url,
            content_hash=binary_hash,
        )
        data = {
            "runtime": fetched.runtime,
            "status_code": fetched.status_code,
            "document_type": "pdf",
            "byte_length": len(body),
            "binary_sha256": binary_hash,
            "page_count": extraction.page_count,
            "pages_extracted": extraction.pages_extracted,
            "text_chars_extracted": len(extraction.text),
            "truncated": extraction.truncated,
            "encrypted": extraction.encrypted,
            "extractor_version": extraction.extractor_version,
            "research_goals": research_goals,
        }
        provenance = {
            "snapshot_id": snapshot.snapshot_id,
            "research_goals": research_goals,
            "document": {
                "format": "pdf",
                "binary_sha256": binary_hash,
                "extractor_version": extraction.extractor_version,
                "ocr_used": False,
            },
        }
        discovery_provider = task.metadata.get("discovery_provider")
        if discovery_provider:
            provenance["discovery"] = {
                "provider": str(discovery_provider),
                "engines": self._string_list(task.metadata.get("discovery_engines")),
                "rank": task.metadata.get("discovery_rank"),
            }

        errors = self._pdf_errors(extraction)
        observation = Observation(
            observation_id=observation_id,
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="pdf_document",
            url=fetched.final_url,
            entity_type="document",
            title=extraction.title or self._url_filename(fetched.final_url),
            text=extraction.text or None,
            data=data,
            content_hash=binary_hash,
            provenance=provenance,
            quality={
                "evidence_backed": True,
                "document": True,
                "text_extracted": bool(extraction.text.strip()),
                "partial": bool(errors),
            },
        )
        evidence_text = extraction.text[:10_000].strip()
        evidence_type = "pdf_text"
        if not evidence_text:
            evidence_type = "pdf_file"
            evidence_text = (
                f"Retrieved PDF document; sha256={binary_hash}; bytes={len(body)}; "
                f"pages={extraction.page_count if extraction.page_count is not None else 'unknown'}"
            )
        evidence = Evidence(
            evidence_id=stable_evidence_id(
                observation_id=observation.observation_id,
                evidence_type=evidence_type,
                source_url=fetched.final_url,
                text=evidence_text,
            ),
            observation_id=observation.observation_id,
            type=evidence_type,
            text=evidence_text,
            source=EvidenceSource(
                provider=self.source_id,
                url=fetched.final_url,
                collected_at=observation.collected_at,
                source_id=self.source_id,
            ),
            metadata={
                "document_type": "pdf",
                "binary_sha256": binary_hash,
                "page_count": extraction.page_count,
                "pages_extracted": extraction.pages_extracted,
                "extractor_version": extraction.extractor_version,
                "ocr_used": False,
                "research_goals": research_goals,
            },
        )
        return SourceResult(
            observations=[observation],
            evidence=[evidence],
            partial=bool(errors),
            errors=errors,
        )

    async def _structured_result(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
        body: bytes,
        extraction: StructuredDataExtraction,
    ) -> SourceResult:
        collection_id = str(task.metadata.get("collection_id", ""))
        research_goals = self._research_goals(task)
        binary_hash = hashlib.sha256(body).hexdigest()
        canonical = self._canonical_structured_payload(extraction.payload)
        snapshot_content = self._structured_snapshot_content(binary_hash, extraction, canonical)
        snapshot = await self.snapshots.capture(
            self.source_id,
            fetched.final_url,
            snapshot_content,
            "application/json; charset=utf-8",
            collection_id=collection_id,
        )
        errors = self._structured_errors(extraction)
        parsed = extraction.payload is not None and extraction.error_code is None
        observation_id = stable_observation_id(
            collection_id=collection_id,
            source_id=self.source_id,
            entity_type="dataset",
            source_url=fetched.final_url,
            content_hash=binary_hash,
        )
        data = {
            "runtime": fetched.runtime,
            "status_code": fetched.status_code,
            "document_type": extraction.document_type,
            "byte_length": len(body),
            "binary_sha256": binary_hash,
            "encoding": extraction.encoding,
            "delimiter": extraction.delimiter,
            "has_header": extraction.has_header,
            "row_count": extraction.row_count,
            "rows_extracted": extraction.rows_extracted,
            "column_count": extraction.column_count,
            "truncated": extraction.truncated,
            "extractor_version": extraction.extractor_version,
            "research_goals": research_goals,
        }
        if parsed:
            data["payload"] = extraction.payload
        if extraction.error_code:
            data["extraction_error_code"] = extraction.error_code

        provenance: dict[str, object] = {
            "snapshot_id": snapshot.snapshot_id,
            "research_goals": research_goals,
            "document": {
                "format": extraction.document_type,
                "binary_sha256": binary_hash,
                "extractor_version": extraction.extractor_version,
                "parser_network_access": False,
            },
        }
        discovery_provider = task.metadata.get("discovery_provider")
        if discovery_provider:
            provenance["discovery"] = {
                "provider": str(discovery_provider),
                "engines": self._string_list(task.metadata.get("discovery_engines")),
                "rank": task.metadata.get("discovery_rank"),
            }

        observation = Observation(
            observation_id=observation_id,
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="structured_data",
            url=fetched.final_url,
            entity_type="dataset",
            title=self._url_filename(fetched.final_url),
            text=canonical[:100_000] or None,
            data=data,
            content_hash=binary_hash,
            provenance=provenance,
            quality={
                "evidence_backed": True,
                "document": True,
                "structured": True,
                "parsed": parsed,
                "partial": bool(errors),
            },
        )
        if canonical:
            evidence_type = "structured_data"
            evidence_text = canonical[:10_000]
        else:
            evidence_type = "structured_file"
            evidence_text = (
                f"Retrieved {extraction.document_type} document; "
                f"sha256={binary_hash}; bytes={len(body)}"
            )
        evidence = Evidence(
            evidence_id=stable_evidence_id(
                observation_id=observation.observation_id,
                evidence_type=evidence_type,
                source_url=fetched.final_url,
                text=evidence_text,
            ),
            observation_id=observation.observation_id,
            type=evidence_type,
            text=evidence_text,
            source=EvidenceSource(
                provider=self.source_id,
                url=fetched.final_url,
                collected_at=observation.collected_at,
                source_id=self.source_id,
            ),
            metadata={
                "document_type": extraction.document_type,
                "binary_sha256": binary_hash,
                "row_count": extraction.row_count,
                "rows_extracted": extraction.rows_extracted,
                "column_count": extraction.column_count,
                "extractor_version": extraction.extractor_version,
                "research_goals": research_goals,
            },
        )
        return SourceResult(
            observations=[observation],
            evidence=[evidence],
            partial=bool(errors),
            errors=errors,
        )

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["pdf_extraction"] = True
        payload["structured_data_extraction"] = self.structured_data_extractor is not None
        if self.structured_data_extractor is not None:
            payload["structured_data_formats"] = ["csv", "tsv", "json"]
        return payload

    @staticmethod
    def _is_pdf(fetched) -> bool:
        content_type = str(fetched.content_type or "").split(";", 1)[0].strip().lower()
        body = fetched.body
        return content_type == "application/pdf" or bool(
            body is not None and body.startswith(b"%PDF-")
        )

    def _structured_type(self, fetched) -> str | None:
        if self.structured_data_extractor is None:
            return None
        return self.structured_data_extractor.detect(
            fetched.content_type,
            fetched.final_url,
            fetched.body,
        )

    @staticmethod
    def _snapshot_content(binary_hash: str, extraction: PdfExtraction) -> str:
        header = [
            f"pdf_sha256={binary_hash}",
            f"page_count={extraction.page_count}",
            f"pages_extracted={extraction.pages_extracted}",
            f"extractor_version={extraction.extractor_version}",
            f"encrypted={str(extraction.encrypted).lower()}",
            f"truncated={str(extraction.truncated).lower()}",
        ]
        if extraction.error_code:
            header.append(f"error_code={extraction.error_code}")
        return "\n".join(header) + "\n\n" + extraction.text

    @staticmethod
    def _structured_snapshot_content(
        binary_hash: str,
        extraction: StructuredDataExtraction,
        canonical: str,
    ) -> str:
        header = {
            "binary_sha256": binary_hash,
            "document_type": extraction.document_type,
            "extractor_version": extraction.extractor_version,
            "encoding": extraction.encoding,
            "delimiter": extraction.delimiter,
            "has_header": extraction.has_header,
            "row_count": extraction.row_count,
            "rows_extracted": extraction.rows_extracted,
            "column_count": extraction.column_count,
            "truncated": extraction.truncated,
            "error_code": extraction.error_code,
        }
        metadata = json.dumps(header, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return metadata + ("\n" + canonical if canonical else "")

    @staticmethod
    def _canonical_structured_payload(payload: object | None) -> str:
        if payload is None:
            return ""
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _pdf_errors(extraction: PdfExtraction) -> list[StructuredError]:
        errors: list[StructuredError] = []
        if extraction.error_code:
            errors.append(
                StructuredError(
                    code=extraction.error_code,
                    message=extraction.error_message or "PDF extraction failed",
                    retryable=extraction.error_code
                    in {"PDF_EXTRACTION_TIMEOUT", "PDF_EXTRACTOR_CRASHED"},
                    source_id="generic_web",
                )
            )
            return errors
        if extraction.truncated:
            errors.append(
                StructuredError(
                    code="PDF_EXTRACTION_TRUNCATED",
                    message="PDF text extraction reached a configured page or text limit",
                    retryable=False,
                    source_id="generic_web",
                )
            )
        if not extraction.text.strip():
            errors.append(
                StructuredError(
                    code="PDF_TEXT_EMPTY",
                    message=(
                        "PDF contains no extractable text in the configured page range; "
                        "OCR is not performed automatically"
                    ),
                    retryable=False,
                    source_id="generic_web",
                )
            )
        return errors

    @staticmethod
    def _structured_errors(extraction: StructuredDataExtraction) -> list[StructuredError]:
        if extraction.error_code:
            return [
                StructuredError(
                    code=extraction.error_code,
                    message=extraction.error_message or "Structured data extraction failed",
                    retryable=False,
                    source_id="generic_web",
                )
            ]
        if extraction.truncated:
            return [
                StructuredError(
                    code="STRUCTURED_DATA_TRUNCATED",
                    message=(
                        "Structured data normalization reached a configured record, column, "
                        "or cell limit"
                    ),
                    retryable=False,
                    source_id="generic_web",
                )
            ]
        return []

    @staticmethod
    def _url_filename(url: str) -> str | None:
        path = unquote(urlparse(url).path)
        name = path.rsplit("/", 1)[-1].strip()
        return name[:1_000] or None

    @staticmethod
    def _string_list(value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        return [str(item) for item in value]
