from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup


@dataclass(slots=True)
class JsonLdEntity:
    block_index: int
    node_index: int
    data: dict[str, Any]


@dataclass(slots=True)
class JsonLdExtraction:
    entities: list[JsonLdEntity]
    blocks_seen: int = 0
    blocks_invalid: int = 0
    blocks_oversized: int = 0


class EmbeddedJsonLdExtractor:
    """Parse bounded embedded JSON-LD without resolving remote contexts.

    The extractor treats JSON-LD as page-declared machine-readable evidence. It does
    not implement JSON-LD expansion/compaction and never dereferences ``@context``.
    """

    def __init__(
        self,
        *,
        max_blocks: int = 20,
        max_block_chars: int = 250_000,
        max_entities: int = 50,
        max_depth: int = 8,
        max_items_per_container: int = 100,
        max_string_chars: int = 10_000,
    ) -> None:
        self.max_blocks = max(1, max_blocks)
        self.max_block_chars = max(1_000, max_block_chars)
        self.max_entities = max(1, max_entities)
        self.max_depth = max(1, max_depth)
        self.max_items_per_container = max(1, max_items_per_container)
        self.max_string_chars = max(100, max_string_chars)

    def extract(self, html: str, content_type: str | None = None) -> JsonLdExtraction:
        if content_type and "html" not in content_type.casefold():
            return JsonLdExtraction(entities=[])

        soup = BeautifulSoup(html, "html.parser")
        entities: list[JsonLdEntity] = []
        blocks_seen = 0
        blocks_invalid = 0
        blocks_oversized = 0

        scripts = soup.find_all("script", attrs={"type": self._is_json_ld_type})
        for block_index, script in enumerate(scripts[: self.max_blocks]):
            blocks_seen += 1
            raw = script.string if script.string is not None else script.get_text()
            text = str(raw or "").strip()
            if not text:
                continue
            if len(text) > self.max_block_chars:
                blocks_oversized += 1
                continue
            try:
                payload = json.loads(text)
            except (json.JSONDecodeError, TypeError, ValueError):
                blocks_invalid += 1
                continue

            for node_index, node in enumerate(self._top_level_nodes(payload)):
                if len(entities) >= self.max_entities:
                    break
                sanitized = self._sanitize(node, depth=0)
                if isinstance(sanitized, dict) and sanitized:
                    entities.append(
                        JsonLdEntity(
                            block_index=block_index,
                            node_index=node_index,
                            data=sanitized,
                        )
                    )
            if len(entities) >= self.max_entities:
                break

        return JsonLdExtraction(
            entities=entities,
            blocks_seen=blocks_seen,
            blocks_invalid=blocks_invalid,
            blocks_oversized=blocks_oversized,
        )

    @staticmethod
    def _is_json_ld_type(value: object) -> bool:
        if not isinstance(value, str):
            return False
        media_type = value.split(";", 1)[0].strip().casefold()
        return media_type == "application/ld+json"

    @classmethod
    def _top_level_nodes(cls, payload: Any) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        if isinstance(payload, dict):
            graph = payload.get("@graph")
            if isinstance(graph, list):
                nodes.extend(item for item in graph if isinstance(item, dict))
                remainder = {key: value for key, value in payload.items() if key != "@graph"}
                if cls._has_entity_fields(remainder):
                    nodes.insert(0, remainder)
            else:
                nodes.append(payload)
        elif isinstance(payload, list):
            nodes.extend(item for item in payload if isinstance(item, dict))
        return nodes

    @staticmethod
    def _has_entity_fields(node: dict[str, Any]) -> bool:
        return any(key in node for key in ("@id", "@type", "name", "headline", "url"))

    def _sanitize(self, value: Any, *, depth: int) -> Any:
        if depth >= self.max_depth:
            return None
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value[: self.max_string_chars]
        if isinstance(value, list):
            items = [
                self._sanitize(item, depth=depth + 1)
                for item in value[: self.max_items_per_container]
            ]
            return [item for item in items if item is not None]
        if isinstance(value, dict):
            result: dict[str, Any] = {}
            for raw_key, raw_value in list(value.items())[: self.max_items_per_container]:
                key = str(raw_key)[:500]
                sanitized = self._sanitize(raw_value, depth=depth + 1)
                if sanitized is not None:
                    result[key] = sanitized
            return result
        return None
