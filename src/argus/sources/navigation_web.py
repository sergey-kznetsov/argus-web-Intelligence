from __future__ import annotations

from copy import copy
from urllib.parse import urldefrag, urlparse

from argus.contracts.models import CollectionRequest
from argus.research.content_navigation import ContentItemNavigationRanker
from argus.sources.base import SourceTask
from argus.sources.office_web import OfficeAwareGenericWebAdapter


class ContentNavigationMixin:
    """Rank allowed child links before the underlying web adapter applies bounded fan-out.

    Search/listing URLs remain valid navigation surfaces. This mixin only changes fetch
    order so likely item destinations are reached before menu, category, search and login
    shells consume a small serial-lane budget. URL ranking is never factual Evidence.
    """

    content_navigation = ContentItemNavigationRanker()

    def _discovered_tasks(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
        collection_id: str,
    ) -> list[SourceTask]:
        candidates = self._ranked_allowed_links(fetched, request)
        if not candidates:
            return super()._discovered_tasks(task, fetched, request, collection_id)

        ranked_fetched = copy(fetched)
        ranked_fetched.links = [item.url for item in candidates]
        discovered = super()._discovered_tasks(
            task,
            ranked_fetched,
            request,
            collection_id,
        )
        scores = {item.url: item.score for item in candidates}
        for child in discovered:
            if child.source_id != self.source_id:
                continue
            score = scores.get(urldefrag(child.url)[0])
            if score is None:
                continue
            child.metadata["content_navigation_score"] = score
            child.metadata["content_navigation_ranking_version"] = self.content_navigation.version
            child.metadata["content_navigation_is_evidence"] = False
        return discovered

    def _ranked_allowed_links(self, fetched, request: CollectionRequest):
        allowed = {
            domain.lower().strip(".")
            for domain in request.constraints.allowed_domains
        }
        denied = {
            domain.lower().strip(".")
            for domain in request.constraints.denied_domains
        }
        seed_host = (urlparse(fetched.final_url).hostname or "").lower().strip(".")
        links: list[str] = []
        seen: set[str] = set()
        for raw in list(fetched.links)[: self.content_navigation.max_links_considered]:
            link = urldefrag(str(raw))[0]
            if not link or link in seen:
                continue
            if not self._domain_allowed(link, seed_host, allowed, denied):
                continue
            seen.add(link)
            links.append(link)
        return self.content_navigation.rank(links)


class ContentNavigationOfficeAwareGenericWebAdapter(
    ContentNavigationMixin,
    OfficeAwareGenericWebAdapter,
):
    """Office-aware web adapter with bounded item-first child navigation."""


__all__ = ["ContentNavigationMixin", "ContentNavigationOfficeAwareGenericWebAdapter"]
