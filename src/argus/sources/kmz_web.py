from __future__ import annotations

import asyncio
import hashlib
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

from argus.contracts.models import CollectionRequest, StructuredError
from argus.extraction.kmz import BoundedKmzExtractor, KmzExtraction
from argus.extraction.structured_data import StructuredDataExtraction
from argus.sources.base import SourceResult, SourceTask
from argus.sources.kml_web import KmlAwareWebAdapter


class KmzAwareWebAdapter(KmlAwareWebAdapter):
    """Add bounded KMZ package handling before the shared KML factual normalizer."""

    _KMZ_MEDIA_TYPES = {
        "application/vnd.google-earth.kmz",
        "application/zip",
        "application/octet-stream",
    }

    def __init__(
        self,
        *args,
        kmz_extractor: BoundedKmzExtractor | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        if kmz_extractor is None:
            compressed_limit = int(self.structured_data_extractor.max_bytes)
            kmz_extractor = BoundedKmzExtractor(
                max_bytes=compressed_limit,
                max_members=min(max(self.structured_data_extractor.max_records, 1), 1_000),
                max_uncompressed_bytes=min(compressed_limit * 4, 20 * 1024 * 1024),
                max_member_bytes=min(compressed_limit * 2, 10 * 1024 * 1024),
                max_kml_bytes=compressed_limit,
            )
        self.kmz_extractor = kmz_extractor

    def _is_document_response(self, fetched) -> bool:
        return self._is_kmz_response(fetched) or super()._is_document_response(fetched)

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        if fetched.blocked or not self._is_kmz_response(fetched):
            return await super().extract(task, fetched, request)

        body = fetched.body
        if body is None:
            return SourceResult(
                observations=[],
                partial=True,
                errors=[
                    StructuredError(
                        code="KMZ_BINARY_UNAVAILABLE",
                        message="KMZ response did not retain its bounded source body",
                        retryable=True,
                        source_id=self.source_id,
                    )
                ],
            )

        kmz_result = await asyncio.to_thread(self.kmz_extractor.extract, body)
        if kmz_result.kml_body is None:
            extraction = StructuredDataExtraction(
                document_type="xml",
                extractor_version=kmz_result.extractor_version,
                error_code=kmz_result.error_code or "KMZ_PACKAGE_INVALID",
                error_message=kmz_result.error_message or "KMZ extraction failed",
            )
        else:
            extraction = await asyncio.to_thread(
                self.structured_data_extractor.extract,
                kmz_result.kml_body,
                content_type="application/vnd.google-earth.kml+xml",
                url="doc.kml",
            )

        result = await self._structured_result(
            task,
            fetched,
            request,
            body,
            extraction,
        )
        metadata = self._attach_kmz_metadata(result, kmz_result, body)
        result = self._normalize_kml_result(
            result,
            task=task,
            request=request,
            source_url=fetched.final_url,
        )
        self._attach_kmz_to_kml_facts(result, metadata)
        return result

    @classmethod
    def _is_kmz_response(cls, fetched) -> bool:
        suffix = PurePosixPath(unquote(urlparse(str(fetched.final_url or "")).path)).suffix.casefold()
        if suffix != ".kmz":
            return False
        body = fetched.body
        if body is not None and body.startswith(b"PK"):
            return True
        media_type = str(fetched.content_type or "").split(";", 1)[0].strip().casefold()
        return media_type in cls._KMZ_MEDIA_TYPES or not media_type

    @staticmethod
    def _attach_kmz_metadata(
        result: SourceResult,
        extraction: KmzExtraction,
        package_body: bytes,
    ) -> dict[str, object]:
        metadata: dict[str, object] = {
            "format": "kmz",
            "package_sha256": hashlib.sha256(package_body).hexdigest(),
            "package_bytes": len(package_body),
            "member_count": extraction.member_count,
            "declared_uncompressed_bytes": extraction.declared_uncompressed_bytes,
            "root_kml": "doc.kml",
            "root_kml_sha256": (
                hashlib.sha256(extraction.kml_body).hexdigest()
                if extraction.kml_body is not None
                else None
            ),
            "root_kml_bytes": len(extraction.kml_body) if extraction.kml_body is not None else None,
            "extractor_version": extraction.extractor_version,
            "error_code": extraction.error_code,
            "resources_resolved": False,
            "network_links_followed": False,
        }
        for observation in result.observations:
            observation.data["container"] = metadata
            document = observation.provenance.setdefault("document", {})
            if isinstance(document, dict):
                document["container"] = metadata
            observation.quality["packaged_source"] = True
        for evidence in result.evidence:
            evidence.metadata["container"] = metadata
        return metadata

    @staticmethod
    def _attach_kmz_to_kml_facts(
        result: SourceResult,
        metadata: dict[str, object],
    ) -> None:
        kml_observation_ids: set[str] = set()
        for observation in result.observations:
            if observation.source_kind != "kml_point":
                continue
            observation.provenance["container"] = metadata
            observation.quality["packaged_source"] = True
            kml_observation_ids.add(observation.observation_id)
        for evidence in result.evidence:
            if evidence.observation_id in kml_observation_ids:
                evidence.metadata["container"] = metadata

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["kmz_extraction"] = True
        payload["kmz_root_kml"] = "doc.kml"
        payload["kmz_resources_resolved"] = False
        payload["kmz_max_members"] = self.kmz_extractor.max_members
        payload["kmz_max_uncompressed_bytes"] = self.kmz_extractor.max_uncompressed_bytes
        return payload
