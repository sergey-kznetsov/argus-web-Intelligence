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

    Mandatory source-contour entry pages use a stricter two-hop policy: an entry that is
    already item-shaped does not fan out; an obvious listing may select one best item-like
    child (or one declared feed when no item link exists). The selected follow-up is terminal
    for in-page navigation. This keeps every independently discovered street/source entry
    eligible for one factual item fetch instead of letting the first listing monopolize the
    serial lane with menu and archive links.
    """

    content_navigation = ContentItemNavigationRanker()
    serial_item_followup_version = "serial-item-followup/1"

    def _discovered_tasks(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
        collection_id: str,
    ) -> list[SourceTask]:
        if task.metadata.get("serial_item_followup_terminal") is True:
            return []

        candidates = self._ranked_allowed_links(fetched, request)
        if not candidates:
            discovered = super()._discovered_tasks(task, fetched, request, collection_id)
            return self._source_contour_followup(task, fetched, discovered)

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
        return self._source_contour_followup(task, fetched, discovered)

    def _source_contour_followup(
        self,
        task: SourceTask,
        fetched,
        discovered: list[SourceTask],
    ) -> list[SourceTask]:
        """Select one bounded item/feed hop for mandatory source-contour entry pages."""

        if not task.metadata.get("source_contour") or task.depth != 0:
            return discovered

        page_url = str(fetched.final_url or task.url)
        if not self.content_navigation.is_navigation_shell(page_url):
            task.metadata["serial_item_followup_policy"] = self.serial_item_followup_version
            task.metadata["serial_item_followup_reason"] = "entry_is_item"
            return []

        item_children = [
            child
            for child in discovered
            if child.source_id == self.source_id
            and not self.content_navigation.is_navigation_shell(child.url)
        ]
        feed_children = [
            child
            for child in discovered
            if child.source_id in {"rss_atom", "json_feed"}
        ]
        selected = item_children[0] if item_children else feed_children[0] if feed_children else None
        if selected is None:
            task.metadata["serial_item_followup_policy"] = self.serial_item_followup_version
            task.metadata["serial_item_followup_reason"] = "no_item_or_feed"
            return []

        selected.metadata["serial_item_followup_terminal"] = True
        selected.metadata["serial_item_followup_policy"] = self.serial_item_followup_version
        selected.metadata["serial_item_followup_parent_url"] = page_url
        selected.metadata["serial_item_followup_kind"] = (
            "item" if selected.source_id == self.source_id else "feed"
        )
        return [selected]

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
