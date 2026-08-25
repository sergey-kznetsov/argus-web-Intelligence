from __future__ import annotations

import asyncio
import ipaddress
import socket
from dataclasses import dataclass, field
from urllib.parse import urlsplit


class UnsafeUrlError(ValueError):
    pass


_CLOUD_METADATA_HOSTS = {
    "169.254.169.254",
    "metadata.google.internal",
    "metadata.azure.internal",
}


@dataclass(slots=True)
class UrlGuard:
    allowed_internal_targets: set[str]
    denied_targets: set[str] = field(default_factory=set)
    public_ports: set[int] = field(default_factory=lambda: {80, 443})

    @classmethod
    def from_strings(
        cls,
        values: list[str],
        *,
        deny_values: list[str] | None = None,
        public_ports: list[int] | None = None,
    ) -> "UrlGuard":
        allowed = {v.lower().strip().strip(".") for v in values if v and v.strip()}
        denied = {
            v.lower().strip().strip(".")
            for v in (deny_values or [])
            if v and v.strip()
        }
        ports = {int(port) for port in (public_ports or [80, 443])}
        if not ports or any(port < 1 or port > 65535 for port in ports):
            raise ValueError("public_ports must contain valid TCP ports")
        return cls(
            allowed_internal_targets=allowed,
            denied_targets=denied,
            public_ports=ports,
        )

    async def validate(self, url: str) -> str:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise UnsafeUrlError("only http/https URLs are allowed")
        if parsed.username or parsed.password:
            raise UnsafeUrlError("userinfo in URL is not allowed")
        host = (parsed.hostname or "").lower().strip(".")
        if not host:
            raise UnsafeUrlError("URL host is required")
        if self._matches_host_policy(host, self.denied_targets):
            raise UnsafeUrlError("target host is denied by outbound policy")
        if host in _CLOUD_METADATA_HOSTS and host not in self.allowed_internal_targets:
            raise UnsafeUrlError("cloud metadata target is blocked")

        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise UnsafeUrlError("URL port is invalid") from exc

        # Explicit internal targets are an operator-controlled exception to the public
        # address and public-port rules, but never to the explicit deny policy above.
        if host in self.allowed_internal_targets:
            return url
        if port not in self.public_ports:
            raise UnsafeUrlError("target port is blocked by outbound policy")

        addresses = await self._resolve(host, port)
        if not addresses:
            raise UnsafeUrlError("host could not be resolved")
        for address in addresses:
            if self._unsafe_ip(address):
                raise UnsafeUrlError("target resolves to a non-public address")
        return url

    async def validate_redirect(self, from_url: str, to_url: str) -> str:
        del from_url
        return await self.validate(to_url)

    @staticmethod
    def _matches_host_policy(host: str, values: set[str]) -> bool:
        return any(host == value or host.endswith("." + value) for value in values)

    @staticmethod
    async def _resolve(host: str, port: int) -> set[str]:
        try:
            literal = ipaddress.ip_address(host)
            return {str(literal)}
        except ValueError:
            pass
        loop = asyncio.get_running_loop()
        try:
            infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise UnsafeUrlError("host could not be resolved") from exc
        return {item[4][0] for item in infos}

    @staticmethod
    def _unsafe_ip(value: str) -> bool:
        ip = ipaddress.ip_address(value)
        return any((
            ip.is_private,
            ip.is_loopback,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_reserved,
            ip.is_unspecified,
        ))
