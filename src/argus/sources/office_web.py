from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from argus.contracts.models import (
    CollectionRequest,
    Evidence,
    EvidenceSource,
    Observation,
    StructuredError,
)
from argus.extraction.ooxml import BoundedOoxmlExtractor
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.sources.base import SourceResult, SourceTask
from argus.sources.document_web import DocumentAwareGenericWebAdapter


class OfficeAwareGenericWebAdapter(DocumentAwareGenericWebAdapter):
    """Handle public Office files without confusing binary formats with text data.

    DOCX/XLSX use a bounded in-memory OOXML extractor. Legacy DOC/XLS stay file-only
    until a separate bounded OLE/BIFF parser is implemented.
    """

    _OFFICE_SUFFIXES = {
        ".doc": "doc",
        ".docx": "docx",
        ".xls": "xls",
        ".xlsx": "xlsx",
    }
    _TEXT_DATA_SUFFIXES = {
        ".csv",
        ".geojson",
        ".json",
        ".kml",
        ".tab",
        ".tsv",
        ".xml",
    }
    _OFFICE_MEDIA_TYPES = {
        "application/msword": "doc",
        "application/vnd.ms-excel": "xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    }

    def __init__(
        self,
        *args,
        ooxml_extractor: BoundedOoxmlExtractor | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.ooxml_extractor = ooxml_extractor

    def _is_document_response(self, fetched) -> bool:
        return self._office_type(fetched) is not None or super()._is_document_response(fetched)

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        if fetched.blocked:
            return await super().extract(task, fetched, request)
        office_type = self._office_type(fetched)
        if office_type is None:
            return await super().extract(task, fetched, request)
        body = fetched.body
        if body is None:
            return SourceResult(
                observations=[],
                partial=True,
                errors=[
                    StructuredError(
                        code="OFFICE_BINARY_UNAVAILABLE",
                        message="Office response did not retain its bounded source body",
                        retryable=True,
                        source_id=self.source_id,
                    )
                ],
            )
        if office_type in {"docx", "xlsx"} and self.ooxml_extractor is not None:
            extraction = await asyncio.to_thread(
                self.ooxml_extractor.extract,
                body,
                document_type=office_type,
            )
            result = await self._structured_result(task, fetched, request, body, extraction)
            return self._retag_ooxml_result(result, office_type, fetched.final_url)
        return await self._office_file_result(task, fetched, request, body, office_type)

    def _retag_ooxml_result(
        self,
        result: SourceResult,
        office_type: str,
        source_url: str,
    ) -> SourceResult:
        entity_type = "document" if office_type == "docx" else "dataset"
        source_kind = "office_document" if office_type == "docx" else "office_spreadsheet"
        for observation in result.observations:
            old_id = observation.observation_id
            observation.entity_type = entity_type
            observation.source_kind = source_kind
            observation.observation_id = stable_observation_id(
                collection_id=observation.collection_id,
                source_id=self.source_id,
                entity_type=entity_type,
                source_url=source_url,
                content_hash=observation.content_hash,
            )
            observation.data["ooxml"] = True
            observation.provenance.setdefault("document", {})["ooxml"] = True
            for evidence in result.evidence:
                if evidence.observation_id != old_id:
                    continue
                evidence.observation_id = observation.observation_id
                evidence.evidence_id = stable_evidence_id(
                    observation_id=observation.observation_id,
                    evidence_type=evidence.type,
                    source_url=source_url,
                    text=evidence.text,
                )
        for error in result.errors:
            if error.code == "STRUCTURED_DATA_TRUNCATED":
                error.code = "OOXML_EXTRACTION_TRUNCATED"
                error.message = "OOXML normalization reached a configured document limit"
        return result

    async def _office_file_result(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
        body: bytes,
        office_type: str,
    ) -> SourceResult:
        collection_id = str(task.metadata.get("collection_id", ""))
        research_goals = self._research_goals(task)
        binary_hash = hashlib.sha256(body).hexdigest()
        snapshot_content = json.dumps(
            {
                "binary_sha256": binary_hash,
                "byte_length": len(body),
                "document_type": office_type,
                "extractor_version": "office-classifier/1",
                "parsed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        snapshot = await self.snapshots.capture(
            self.source_id,
            fetched.final_url,
            snapshot_content,
            "application/json; charset=utf-8",
            collection_id=collection_id,
        )
        observation_id = stable_observation_id(
            collection_id=collection_id,
            source_id=self.source_id,
            entity_type="document",
            source_url=fetched.final_url,
            content_hash=binary_hash,
        )
        observation = Observation(
            observation_id=observation_id,
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="office_document_file",
            url=fetched.final_url,
            entity_type="document",
            title=self._url_filename(fetched.final_url),
            data={
                "runtime": fetched.runtime,
                "status_code": fetched.status_code,
                "document_type": office_type,
                "byte_length": len(body),
                "binary_sha256": binary_hash,
                "parsed": False,
                "extractor_version": "office-classifier/1",
                "research_goals": research_goals,
            },
            content_hash=binary_hash,
            provenance={
                "snapshot_id": snapshot.snapshot_id,
                "research_goals": research_goals,
                "document": {
                    "format": office_type,
                    "binary_sha256": binary_hash,
                    "extractor_version": "office-classifier/1",
                    "parsed": False,
                    "parser_network_access": False,
                },
            },
            quality={
                "evidence_backed": True,
                "document": True,
                "parsed": False,
                "partial": True,
            },
        )
        evidence_text = (
            f"Retrieved {office_type} document; sha256={binary_hash}; bytes={len(body)}"
        )
        evidence = Evidence(
            evidence_id=stable_evidence_id(
                observation_id=observation.observation_id,
                evidence_type="office_file",
                source_url=fetched.final_url,
                text=evidence_text,
            ),
            observation_id=observation.observation_id,
            type="office_file",
            text=evidence_text,
            source=EvidenceSource(
                provider=self.source_id,
                url=fetched.final_url,
                collected_at=observation.collected_at,
                source_id=self.source_id,
            ),
            metadata={
                "document_type": office_type,
                "binary_sha256": binary_hash,
                "byte_length": len(body),
                "parsed": False,
                "extractor_version": "office-classifier/1",
                "research_goals": research_goals,
            },
        )
        return SourceResult(
            observations=[observation],
            evidence=[evidence],
            partial=True,
            errors=[
                StructuredError(
                    code="OFFICE_FORMAT_NOT_PARSED",
                    message=(
                        f"{office_type.upper()} was retrieved but no bounded content parser "
                        "is enabled for this format"
                    ),
                    retryable=False,
                    source_id=self.source_id,
                )
            ],
        )

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["office_document_detection"] = ["doc", "docx", "xls", "xlsx"]
        payload["office_document_extraction"] = (
            ["docx", "xlsx"] if self.ooxml_extractor is not None else []
        )
        payload["office_file_only_formats"] = ["doc", "xls"]
        return payload

    @classmethod
    def _office_type(cls, fetched) -> str | None:
        suffix = PurePosixPath(unquote(urlparse(fetched.final_url).path)).suffix.casefold()
        if suffix in cls._OFFICE_SUFFIXES:
            return cls._OFFICE_SUFFIXES[suffix]
        if suffix in cls._TEXT_DATA_SUFFIXES:
            return None
        media_type = str(fetched.content_type or "").split(";", 1)[0].strip().casefold()
        return cls._OFFICE_MEDIA_TYPES.get(media_type)
