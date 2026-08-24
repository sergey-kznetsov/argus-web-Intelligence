from __future__ import annotations

from dataclasses import dataclass
import zlib


@dataclass(slots=True)
class GzipExtraction:
    body: bytes | None = None
    compressed_bytes: int = 0
    uncompressed_bytes: int = 0
    extractor_version: str = "zlib-gzip/1"
    error_code: str | None = None
    error_message: str | None = None


class BoundedGzipExtractor:
    """Bounded single-member gzip decompressor for public structured artifacts.

    The input is already transport-bounded, but compressed and decompressed limits are
    checked independently. Concatenated gzip members and any trailing bytes are rejected
    so one source artifact cannot silently turn into multiple joined datasets.
    """

    _GZIP_MAGIC = b"\x1f\x8b"

    def __init__(
        self,
        *,
        max_compressed_bytes: int = 5 * 1024 * 1024,
        max_uncompressed_bytes: int = 5 * 1024 * 1024,
        input_chunk_bytes: int = 64 * 1024,
    ) -> None:
        self.max_compressed_bytes = max(1, int(max_compressed_bytes))
        self.max_uncompressed_bytes = max(1, int(max_uncompressed_bytes))
        self.input_chunk_bytes = max(1, int(input_chunk_bytes))

    def extract(self, body: bytes) -> GzipExtraction:
        compressed_bytes = len(body)
        if compressed_bytes > self.max_compressed_bytes:
            return GzipExtraction(
                compressed_bytes=compressed_bytes,
                error_code="GZIP_COMPRESSED_TOO_LARGE",
                error_message="Gzip source exceeds configured compressed byte limit",
            )
        if not body.startswith(self._GZIP_MAGIC):
            return GzipExtraction(
                compressed_bytes=compressed_bytes,
                error_code="GZIP_INVALID",
                error_message="Source does not contain a valid gzip header",
            )

        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        output = bytearray()
        offset = 0
        pending = b""
        try:
            while True:
                if pending:
                    chunk = pending
                    pending = b""
                elif offset < compressed_bytes:
                    chunk = body[offset : offset + self.input_chunk_bytes]
                    offset += len(chunk)
                else:
                    break

                remaining = self.max_uncompressed_bytes - len(output)
                produced = decoder.decompress(chunk, max(1, remaining + 1))
                output.extend(produced)
                if len(output) > self.max_uncompressed_bytes:
                    return GzipExtraction(
                        compressed_bytes=compressed_bytes,
                        uncompressed_bytes=len(output),
                        error_code="GZIP_UNCOMPRESSED_LIMIT_EXCEEDED",
                        error_message=(
                            "Gzip output exceeds configured uncompressed byte limit"
                        ),
                    )

                pending = decoder.unconsumed_tail
                if decoder.eof:
                    trailing = decoder.unused_data + pending + body[offset:]
                    if trailing:
                        return GzipExtraction(
                            compressed_bytes=compressed_bytes,
                            uncompressed_bytes=len(output),
                            error_code="GZIP_TRAILING_DATA",
                            error_message=(
                                "Gzip artifact contains trailing data or additional members"
                            ),
                        )
                    break

            if not decoder.eof:
                return GzipExtraction(
                    compressed_bytes=compressed_bytes,
                    uncompressed_bytes=len(output),
                    error_code="GZIP_TRUNCATED",
                    error_message="Gzip stream ended before a complete member trailer",
                )

            remaining = self.max_uncompressed_bytes - len(output)
            flushed = decoder.flush(max(1, remaining + 1))
            output.extend(flushed)
            if len(output) > self.max_uncompressed_bytes:
                return GzipExtraction(
                    compressed_bytes=compressed_bytes,
                    uncompressed_bytes=len(output),
                    error_code="GZIP_UNCOMPRESSED_LIMIT_EXCEEDED",
                    error_message=(
                        "Gzip output exceeds configured uncompressed byte limit"
                    ),
                )
        except zlib.error as exc:
            return GzipExtraction(
                compressed_bytes=compressed_bytes,
                uncompressed_bytes=len(output),
                error_code="GZIP_INVALID",
                error_message=f"Gzip stream failed integrity/decompression checks: {type(exc).__name__}",
            )

        return GzipExtraction(
            body=bytes(output),
            compressed_bytes=compressed_bytes,
            uncompressed_bytes=len(output),
        )
