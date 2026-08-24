from __future__ import annotations

import asyncio
import hashlib
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse, urlunparse

from argus.contracts.models import CollectionRequest, StructuredError
from argus.extraction.gzip_data import BoundedGzipExtractor, GzipExtraction
from argus.extraction.structured_data import StructuredDataExtraction
from argus.sources.base import SourceResult, SourceTask
from argus.sources.office_web import OfficeAwareGenericWebAdapter


class CompressedOfficeAwareGenericWebAdapter(OfficeAwareGenericWebAdapter):
    """Add bounded single-member gzip support for public structured documents."""

    _GZIP_MEDIA_TYPES = {
        "application/gzip",
        "application/x-gzip",
        "application/octet-stream",
    }
    _GZIP_INNER_SUFFIXES = {
        ".csv",
        ".tsv",
        ".tab",
        ".json",
        ".geojson",
        ".xml",
        ".kml",
    }

    def __init__(
        self,
        *args,
        gzip_extractor: BoundedGzipExtractor | None = None,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        if gzip_extractor is None:
            limit = int(self.structured_data_extractor.max_bytes)
            gzip_extractor = BoundedGzipExtractor(
                max_compressed_bytes=limit,
                max_uncompressed_bytes=limit,
            )
        self.gzip_extractor = gzip_extractor

    def _is_document_response(self, fetched) -> bool:
        return self._gzip_inner_url(fetched) is not None or super()._is_document_response(fetched)

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        if fetched.blocked:
            return await super().extract(task, fetched, request)
        inner_url = self._gzip_inner_url(fetched)
        if inner_url is None:
            return await super().extract(task, fetched, request)

        body = fetched.body
        if body is None:
            return SourceResult(
                observations=[],
                partial=True,
                errors=[
                    StructuredError(
                        code="GZIP_BINARY_UNAVAILABLE",
                        message="Gzip response did not retain its bounded source body",
                        retryable=True,
                        source_id=self.source_id,
                    )
                ],
            )

        gzip_result = await asyncio.to_thread(self.gzip_extractor.extract, body)
        if gzip_result.body is None:
            extraction = StructuredDataExtraction(
                document_type=self._inner_document_type(inner_url),
                extractor_version=gzip_result.extractor_version,
                error_code=gzip_result.error_code or "GZIP_INVALID",
                error_message=gzip_result.error_message or "Gzip extraction failed",
            )
        else:
            extraction = await asyncio.to_thread(
                self.structured_data_extractor.extract,
                gzip_result.body,
                content_type=None,
                url=inner_url,
            )

        result = await self._structured_result(
            task,
            fetched,
            request,
            body,
            extraction,
        )
        self._attach_gzip_metadata(result, gzip_result, body, inner_url)
        return result

    def _gzip_inner_url(self, fetched) -> str | None:
        inner_url = self._inner_url(str(fetched.final_url or ""))
        if inner_url is None:
            return None
        body = fetched.body
        if body is not None and body.startswith(b"\x1f\x8b"):
            return inner_url
        media_type = str(fetched.content_type or "").split(";", 1)[0].strip().casefold()
        if media_type in self._GZIP_MEDIA_TYPES or not media_type:
            return inner_url
        return None

    @classmethod
    def _inner_url(cls, source_url: str) -> str | None:
        parsed = urlparse(source_url)
        path = unquote(parsed.path)
        pure = PurePosixPath(path)
        if pure.suffix.casefold() != ".gz":
            return None
        inner_path = str(pure.with_suffix(""))
        if PurePosixPath(inner_path).suffix.casefold() not in cls._GZIP_INNER_SUFFIXES:
            return None
        encoded_path = parsed.path[: -len(pure.suffix)]
        return urlunparse(parsed._replace(path=encoded_path))

    def _inner_document_type(self, inner_url: str) -> str:
        return self.structured_data_extractor.detect(None, inner_url, None) or "unknown"

    @staticmethod
    def _attach_gzip_metadata(
        result: SourceResult,
        gzip_result: GzipExtraction,
        compressed_body: bytes,
        inner_url: str,
    ) -> None:
        uncompressed_sha256 = (
            hashlib.sha256(gzip_result.body).hexdigest()
            if gzip_result.body is not None
            else None
        )
        metadata = {
            "format": "gzip",
            "single_member_required": True,
            "compressed_bytes": len(compressed_body),
            "uncompressed_bytes": gzip_result.uncompressed_bytes,
            "compressed_sha256": hashlib.sha256(compressed_body).hexdigest(),
            "uncompressed_sha256": uncompressed_sha256,
            "inner_url": inner_url,
            "extractor_version": gzip_result.extractor_version,
            "error_code": gzip_result.error_code,
        }
        for observation in result.observations:
            observation.data["compression"] = metadata
            document = observation.provenance.setdefault("document", {})
            if isinstance(document, dict):
                document["compression"] = metadata
            observation.quality["compressed_source"] = True
        for evidence in result.evidence:
            evidence.metadata["compression"] = metadata

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["gzip_structured_data_extraction"] = True
        payload["gzip_structured_data_formats"] = ["csv", "tsv", "json", "xml", "kml"]
        payload["gzip_single_member_only"] = True
        return payload
