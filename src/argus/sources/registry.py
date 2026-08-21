from __future__ import annotations

from argus.sources.base import SourceAdapter


class SourceRegistry:
    def __init__(self) -> None:
        self._sources: dict[str, SourceAdapter] = {}

    def register(self, adapter: SourceAdapter) -> None:
        if adapter.source_id in self._sources:
            raise ValueError(f"duplicate source_id: {adapter.source_id}")
        self._sources[adapter.source_id] = adapter

    def get(self, source_id: str) -> SourceAdapter:
        return self._sources[source_id]

    def all(self) -> list[SourceAdapter]:
        return list(self._sources.values())

    def for_intents(self, intents: list[str]) -> list[SourceAdapter]:
        wanted = set(intents)
        return [source for source in self._sources.values() if source.intents & wanted or "*" in source.intents]
