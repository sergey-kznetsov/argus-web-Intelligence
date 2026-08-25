from __future__ import annotations

import stat
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath


@dataclass(slots=True)
class KmzExtraction:
    kml_body: bytes | None = None
    member_count: int = 0
    declared_uncompressed_bytes: int = 0
    extractor_version: str = "kmz-stdlib/1"
    error_code: str | None = None
    error_message: str | None = None

    @property
    def ok(self) -> bool:
        return self.error_code is None and self.kml_body is not None


@dataclass(slots=True)
class _KmzError(Exception):
    code: str
    message: str

    def __str__(self) -> str:
        return self.message


class BoundedKmzExtractor:
    """Read a KMZ package entirely in memory and return only root ``doc.kml``.

    All ZIP members participate in package-size and path validation, but ARGUS does
    not resolve resource files, overlays or linked KML documents from the package.
    """

    _ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}

    def __init__(
        self,
        *,
        max_bytes: int = 5 * 1024 * 1024,
        max_members: int = 256,
        max_uncompressed_bytes: int = 20 * 1024 * 1024,
        max_member_bytes: int = 10 * 1024 * 1024,
        max_kml_bytes: int = 5 * 1024 * 1024,
    ) -> None:
        self.max_bytes = max(1, int(max_bytes))
        self.max_members = max(1, int(max_members))
        self.max_uncompressed_bytes = max(1, int(max_uncompressed_bytes))
        self.max_member_bytes = max(1, int(max_member_bytes))
        self.max_kml_bytes = max(1, int(max_kml_bytes))

    def extract(self, body: bytes) -> KmzExtraction:
        if len(body) > self.max_bytes:
            return self._error("KMZ_PACKAGE_TOO_LARGE", "KMZ exceeds the compressed byte limit")
        try:
            with zipfile.ZipFile(BytesIO(body), mode="r") as package:
                members, declared_total = self._preflight(package)
                info = members.get("doc.kml")
                if info is None:
                    raise _KmzError(
                        "KMZ_DOC_KML_MISSING",
                        "KMZ package does not contain root doc.kml",
                    )
                if info.file_size > self.max_kml_bytes:
                    raise _KmzError(
                        "KMZ_KML_TOO_LARGE",
                        "KMZ doc.kml exceeds the configured KML byte limit",
                    )
                try:
                    with package.open(info, mode="r") as stream:
                        kml_body = stream.read(self.max_kml_bytes + 1)
                except (RuntimeError, NotImplementedError, OSError, zipfile.BadZipFile) as exc:
                    raise _KmzError(
                        "KMZ_MEMBER_READ_FAILED",
                        f"KMZ doc.kml could not be read: {type(exc).__name__}",
                    ) from exc
                if len(kml_body) > self.max_kml_bytes:
                    raise _KmzError(
                        "KMZ_KML_TOO_LARGE",
                        "KMZ doc.kml exceeds the configured KML byte limit",
                    )
                return KmzExtraction(
                    kml_body=kml_body,
                    member_count=len(members),
                    declared_uncompressed_bytes=declared_total,
                    extractor_version=(
                        f"kmz-stdlib/1;members={len(members)};"
                        f"declared_uncompressed={declared_total}"
                    ),
                )
        except _KmzError as exc:
            return self._error(exc.code, exc.message)
        except (zipfile.BadZipFile, zipfile.LargeZipFile, OSError, RuntimeError, ValueError) as exc:
            return self._error(
                "KMZ_PACKAGE_INVALID",
                f"KMZ package is invalid: {type(exc).__name__}",
            )

    def _preflight(self, package: zipfile.ZipFile) -> tuple[dict[str, zipfile.ZipInfo], int]:
        infos = [item for item in package.infolist() if not item.is_dir()]
        if not infos:
            raise _KmzError("KMZ_PACKAGE_EMPTY", "KMZ package contains no file members")
        if len(infos) > self.max_members:
            raise _KmzError(
                "KMZ_MEMBER_LIMIT_EXCEEDED",
                "KMZ package contains more members than the configured limit",
            )

        members: dict[str, zipfile.ZipInfo] = {}
        casefolded: set[str] = set()
        declared_total = 0
        for info in infos:
            name = self._safe_member_name(info.filename)
            folded = name.casefold()
            if name in members or folded in casefolded:
                raise _KmzError(
                    "KMZ_DUPLICATE_MEMBER",
                    "KMZ contains duplicate or case-colliding member names",
                )
            if info.flag_bits & 0x1:
                raise _KmzError("KMZ_ENCRYPTED_MEMBER", "Encrypted KMZ members are not supported")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if unix_mode and stat.S_IFMT(unix_mode) == stat.S_IFLNK:
                raise _KmzError("KMZ_SYMLINK_MEMBER", "KMZ symbolic-link members are not supported")
            if info.compress_type not in self._ALLOWED_COMPRESSION:
                raise _KmzError(
                    "KMZ_COMPRESSION_UNSUPPORTED",
                    "KMZ member uses an unsupported ZIP compression method",
                )
            if info.file_size < 0 or info.file_size > self.max_member_bytes:
                raise _KmzError(
                    "KMZ_MEMBER_TOO_LARGE",
                    "KMZ member exceeds the configured uncompressed member limit",
                )
            declared_total += info.file_size
            if declared_total > self.max_uncompressed_bytes:
                raise _KmzError(
                    "KMZ_UNCOMPRESSED_LIMIT_EXCEEDED",
                    "KMZ exceeds the configured total uncompressed byte limit",
                )
            members[name] = info
            casefolded.add(folded)
        return members, declared_total

    @staticmethod
    def _safe_member_name(raw_name: str) -> str:
        if not raw_name or "\\" in raw_name or "\x00" in raw_name:
            raise _KmzError("KMZ_MEMBER_PATH_INVALID", "KMZ contains an unsafe member path")
        path = PurePosixPath(raw_name)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise _KmzError("KMZ_MEMBER_PATH_INVALID", "KMZ contains an unsafe member path")
        if ":" in path.parts[0]:
            raise _KmzError("KMZ_MEMBER_PATH_INVALID", "KMZ contains an unsafe member path")
        return path.as_posix()

    @staticmethod
    def _error(code: str, message: str) -> KmzExtraction:
        return KmzExtraction(error_code=code, error_message=message)
