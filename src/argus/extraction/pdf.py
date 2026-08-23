from __future__ import annotations

import multiprocessing as mp
from dataclasses import dataclass
from io import BytesIO
from multiprocessing.connection import Connection
from typing import Any


@dataclass(frozen=True, slots=True)
class PdfExtraction:
    text: str = ""
    title: str | None = None
    page_count: int | None = None
    pages_extracted: int = 0
    truncated: bool = False
    encrypted: bool = False
    extractor_version: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return self.error_code is None


def _apply_memory_limit(memory_mb: int) -> None:
    try:
        import resource
    except ImportError:
        return
    memory_bytes = max(128, int(memory_mb)) * 1024 * 1024
    try:
        resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))
    except (OSError, ValueError):
        # Windows does not expose resource, while some Unix/container configurations do
        # not permit lowering RLIMIT_AS. The parent wall-clock/process isolation still applies.
        return


def _metadata_title(metadata: object) -> str | None:
    if metadata is None:
        return None
    try:
        value = getattr(metadata, "title", None)
    except Exception:
        return None
    if value is None:
        return None
    title = str(value).strip()
    return title[:1_000] or None


def _send(connection: Connection, payload: dict[str, Any]) -> None:
    try:
        connection.send(payload)
    finally:
        connection.close()


def _extract_pdf_worker(
    connection: Connection,
    body: bytes,
    max_pages: int,
    max_text_chars: int,
    memory_mb: int,
) -> None:
    _apply_memory_limit(memory_mb)
    try:
        import pypdf
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(body), strict=False)
        encrypted = bool(reader.is_encrypted)
        if encrypted:
            try:
                decrypted = reader.decrypt("")
            except Exception:
                decrypted = 0
            if not decrypted:
                _send(
                    connection,
                    {
                        "encrypted": True,
                        "extractor_version": f"pypdf/{pypdf.__version__}",
                        "error_code": "PDF_ENCRYPTED",
                        "error_message": "PDF requires a password",
                    },
                )
                return

        page_count = len(reader.pages)
        page_limit = min(page_count, max_pages)
        chunks: list[str] = []
        text_chars = 0
        pages_extracted = 0
        truncated = page_count > page_limit

        for page_index in range(page_limit):
            remaining = max_text_chars - text_chars
            if remaining <= 0:
                truncated = True
                break
            page_text = reader.pages[page_index].extract_text() or ""
            pages_extracted += 1
            if len(page_text) > remaining:
                page_text = page_text[:remaining]
                truncated = True
            if page_text:
                chunks.append(page_text)
                text_chars += len(page_text)

        _send(
            connection,
            {
                "text": "\n\n".join(chunks),
                "title": _metadata_title(reader.metadata),
                "page_count": page_count,
                "pages_extracted": pages_extracted,
                "truncated": truncated,
                "encrypted": encrypted,
                "extractor_version": f"pypdf/{pypdf.__version__}",
            },
        )
    except MemoryError:
        _send(
            connection,
            {
                "error_code": "PDF_MEMORY_LIMIT",
                "error_message": "PDF extraction exceeded its memory limit",
            },
        )
    except BaseException as exc:
        _send(
            connection,
            {
                "error_code": "PDF_PARSE_ERROR",
                "error_message": f"PDF extraction failed: {type(exc).__name__}",
            },
        )


class BoundedPdfExtractor:
    """Parse untrusted PDF bytes in a short-lived resource-bounded child process."""

    def __init__(
        self,
        *,
        max_bytes: int,
        max_pages: int,
        max_text_chars: int,
        timeout_seconds: float,
        memory_mb: int,
    ) -> None:
        self.max_bytes = max(1, int(max_bytes))
        self.max_pages = max(1, int(max_pages))
        self.max_text_chars = max(1, int(max_text_chars))
        self.timeout_seconds = max(0.1, float(timeout_seconds))
        self.memory_mb = max(128, int(memory_mb))

    def extract(self, body: bytes) -> PdfExtraction:
        if len(body) > self.max_bytes:
            return PdfExtraction(
                error_code="PDF_TOO_LARGE",
                error_message="PDF exceeds the configured extraction byte limit",
            )
        if not body.startswith(b"%PDF-"):
            return PdfExtraction(
                error_code="PDF_SIGNATURE_INVALID",
                error_message="Response does not contain a valid PDF header",
            )

        context = mp.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_extract_pdf_worker,
            args=(
                sender,
                body,
                self.max_pages,
                self.max_text_chars,
                self.memory_mb,
            ),
            name="argus-pdf-extractor",
            daemon=True,
        )
        process.start()
        sender.close()
        payload: dict[str, Any] | None = None
        try:
            if receiver.poll(self.timeout_seconds):
                try:
                    payload = receiver.recv()
                except EOFError:
                    payload = None
            else:
                return PdfExtraction(
                    error_code="PDF_EXTRACTION_TIMEOUT",
                    error_message="PDF extraction exceeded its wall-clock limit",
                )
        finally:
            receiver.close()
            process.join(timeout=0.25)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1.0)
            if process.is_alive() and hasattr(process, "kill"):
                process.kill()
                process.join(timeout=1.0)

        if not isinstance(payload, dict):
            return PdfExtraction(
                error_code="PDF_EXTRACTOR_CRASHED",
                error_message="PDF extractor exited without a result",
            )
        return PdfExtraction(
            text=str(payload.get("text") or "")[: self.max_text_chars],
            title=(str(payload["title"])[:1_000] if payload.get("title") else None),
            page_count=(int(payload["page_count"]) if payload.get("page_count") is not None else None),
            pages_extracted=max(0, int(payload.get("pages_extracted") or 0)),
            truncated=bool(payload.get("truncated", False)),
            encrypted=bool(payload.get("encrypted", False)),
            extractor_version=(
                str(payload["extractor_version"])
                if payload.get("extractor_version")
                else None
            ),
            error_code=(str(payload["error_code"]) if payload.get("error_code") else None),
            error_message=(
                str(payload["error_message"])[:500]
                if payload.get("error_message")
                else None
            ),
        )
