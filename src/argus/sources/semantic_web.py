from __future__ import annotations

import json

from argus.contracts.models import CollectionRequest, Evidence, EvidenceSource, Observation
from argus.extraction.html_tables import HtmlTableExtraction, extract_html_tables
from argus.extraction.microdata import MicrodataExtraction, extract_microdata
from argus.history.snapshots import sha256_text
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.sources.base import SourceResult, SourceTask
from argus.sources.office_web import OfficeAwareGenericWebAdapter


class SemanticWebAdapter(OfficeAwareGenericWebAdapter):
    """Add bounded semantic HTML structures to the generic factual web result.

    The parent adapter remains responsible for fetch, document classification,
    snapshots and ordinary page extraction. This layer adds explicitly semantic HTML
    structures and reuses the already-published page snapshot for provenance.
    """

    def __init__(
        self,
        *args,
        html_table_max_scan_chars: int = 1_000_000,
        html_table_max_tables: int = 20,
        html_table_max_rows_per_table: int = 200,
        html_table_max_total_rows: int = 1_000,
        html_table_max_columns: int = 50,
        html_table_max_cell_chars: int = 5_000,
        microdata_max_scan_chars: int = 750_000,
        microdata_max_items: int = 100,
        microdata_max_properties_per_item: int = 100,
        microdata_max_values_per_property: int = 20,
        microdata_max_value_chars: int = 10_000,
        **kwargs,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.html_table_max_scan_chars = max(1, int(html_table_max_scan_chars))
        self.html_table_max_tables = max(1, int(html_table_max_tables))
        self.html_table_max_rows_per_table = max(1, int(html_table_max_rows_per_table))
        self.html_table_max_total_rows = max(1, int(html_table_max_total_rows))
        self.html_table_max_columns = max(1, int(html_table_max_columns))
        self.html_table_max_cell_chars = max(1, int(html_table_max_cell_chars))
        self.microdata_max_scan_chars = max(1, int(microdata_max_scan_chars))
        self.microdata_max_items = max(1, int(microdata_max_items))
        self.microdata_max_properties_per_item = max(
            1, int(microdata_max_properties_per_item)
        )
        self.microdata_max_values_per_property = max(
            1, int(microdata_max_values_per_property)
        )
        self.microdata_max_value_chars = max(1, int(microdata_max_value_chars))

    async def extract(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
    ) -> SourceResult:
        result = await super().extract(task, fetched, request)
        if fetched.blocked or not self._html_candidate(fetched):
            return result

        table_extraction = extract_html_tables(
            fetched.text,
            content_type=fetched.content_type,
            max_scan_chars=self.html_table_max_scan_chars,
            max_tables=self.html_table_max_tables,
            max_rows_per_table=self.html_table_max_rows_per_table,
            max_total_rows=self.html_table_max_total_rows,
            max_columns=self.html_table_max_columns,
            max_cell_chars=self.html_table_max_cell_chars,
        )
        microdata_extraction = extract_microdata(
            fetched.text,
            content_type=fetched.content_type,
            base_url=fetched.final_url,
            max_scan_chars=self.microdata_max_scan_chars,
            max_items=self.microdata_max_items,
            max_properties_per_item=self.microdata_max_properties_per_item,
            max_values_per_property=self.microdata_max_values_per_property,
            max_value_chars=self.microdata_max_value_chars,
        )
        self._attach_table_summary(result, table_extraction)
        self._attach_microdata_summary(result, microdata_extraction)
        if not table_extraction.tables and not microdata_extraction.items:
            return result

        snapshot_id = self._snapshot_id(result)
        if snapshot_id is None:
            # Structured HTML without the parent page snapshot would weaken the
            # evidence contract. Keep the already extracted parent result instead.
            return result

        collection_id = str(task.metadata.get("collection_id", ""))
        research_goals = self._research_goals(task)
        table_observations, table_evidence = self._table_observations(
            table_extraction,
            collection_id=collection_id,
            request=request,
            page_url=fetched.final_url,
            snapshot_id=snapshot_id,
            research_goals=research_goals,
        )
        microdata_observations, microdata_evidence = self._microdata_observations(
            microdata_extraction,
            collection_id=collection_id,
            request=request,
            page_url=fetched.final_url,
            snapshot_id=snapshot_id,
            research_goals=research_goals,
        )
        result.observations.extend(table_observations)
        result.observations.extend(microdata_observations)
        result.evidence.extend(table_evidence)
        result.evidence.extend(microdata_evidence)
        return result

    @staticmethod
    def _html_candidate(fetched) -> bool:
        content_type = (fetched.content_type or "").casefold()
        if content_type:
            return "html" in content_type
        sample = (fetched.text or "").casefold()
        return "<table" in sample or "itemscope" in sample

    @staticmethod
    def _snapshot_id(result: SourceResult) -> str | None:
        for observation in result.observations:
            value = observation.provenance.get("snapshot_id")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _attach_table_summary(
        result: SourceResult,
        extraction: HtmlTableExtraction,
    ) -> None:
        summary = {
            "tables_seen": extraction.tables_seen,
            "tables_extracted": len(extraction.tables),
            "layout_skipped": extraction.layout_skipped,
            "complex_skipped": extraction.complex_skipped,
            "empty_skipped": extraction.empty_skipped,
            "truncated": extraction.truncated,
            "extractor": extraction.extractor_version,
        }
        for observation in result.observations:
            if observation.source_kind == "web_page":
                observation.data["html_table_summary"] = summary
                break

    @staticmethod
    def _attach_microdata_summary(
        result: SourceResult,
        extraction: MicrodataExtraction,
    ) -> None:
        summary = {
            "items_seen": extraction.items_seen,
            "items_extracted": len(extraction.items),
            "items_skipped": extraction.items_skipped,
            "itemref_skipped": extraction.itemref_skipped,
            "truncated": extraction.truncated,
            "extractor": extraction.extractor_version,
        }
        for observation in result.observations:
            if observation.source_kind == "web_page":
                observation.data["microdata_summary"] = summary
                break

    def _table_observations(
        self,
        extraction: HtmlTableExtraction,
        *,
        collection_id: str,
        request: CollectionRequest,
        page_url: str,
        snapshot_id: str,
        research_goals: list[str],
    ) -> tuple[list[Observation], list[Evidence]]:
        observations: list[Observation] = []
        evidence_items: list[Evidence] = []
        for table in extraction.tables:
            table_data = {
                "caption": table.caption,
                "headers": table.headers,
                "rows": table.rows,
                "column_count": table.column_count,
                "truncated": table.truncated,
            }
            canonical = json.dumps(
                table_data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            content_hash = sha256_text(canonical)
            entity_id = f"{page_url}#argus-html-table-{table.index}"
            observation_id = stable_observation_id(
                collection_id=collection_id,
                source_id=self.source_id,
                entity_type="dataset",
                entity_id=entity_id,
                source_url=page_url,
                content_hash=content_hash,
            )
            observation = Observation(
                observation_id=observation_id,
                collection_id=collection_id,
                analysis_id=request.analysis_id,
                consumer=request.consumer,
                source=self.source_id,
                source_kind="html_table",
                url=page_url,
                entity_type="dataset",
                entity_id=entity_id,
                title=table.caption,
                data=table_data,
                content_hash=content_hash,
                provenance={
                    "snapshot_id": snapshot_id,
                    "page_url": page_url,
                    "table_index": table.index,
                    "research_goals": research_goals,
                    "extractor": extraction.extractor_version,
                    "extraction_truncated": extraction.truncated,
                    "table_truncated": table.truncated,
                    "layout_tables_skipped": extraction.layout_skipped,
                    "complex_tables_skipped": extraction.complex_skipped,
                },
                quality={
                    "evidence_backed": True,
                    "machine_readable": True,
                    "source_declared": True,
                    "lossless": not table.truncated,
                },
            )
            evidence_text = canonical[:10_000]
            evidence_item = Evidence(
                evidence_id=stable_evidence_id(
                    observation_id=observation.observation_id,
                    evidence_type="html_table",
                    source_url=page_url,
                    text=evidence_text,
                ),
                observation_id=observation.observation_id,
                type="html_table",
                text=evidence_text,
                source=EvidenceSource(
                    provider=self.source_id,
                    url=page_url,
                    collected_at=observation.collected_at,
                    source_id=self.source_id,
                ),
                metadata={
                    "snapshot_id": snapshot_id,
                    "table_index": table.index,
                    "research_goals": research_goals,
                    "extractor": extraction.extractor_version,
                    "canonical_sha256": content_hash,
                    "evidence_excerpt_truncated": len(canonical) > len(evidence_text),
                },
            )
            observations.append(observation)
            evidence_items.append(evidence_item)
        return observations, evidence_items

    def _microdata_observations(
        self,
        extraction: MicrodataExtraction,
        *,
        collection_id: str,
        request: CollectionRequest,
        page_url: str,
        snapshot_id: str,
        research_goals: list[str],
    ) -> tuple[list[Observation], list[Evidence]]:
        observations: list[Observation] = []
        evidence_items: list[Evidence] = []
        for item in extraction.items:
            item_data = {
                "item_types": item.item_types,
                "item_id": item.item_id,
                "properties": item.properties,
                "truncated": item.truncated,
            }
            canonical = json.dumps(
                item_data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            content_hash = sha256_text(canonical)
            entity_id = item.item_id or f"{page_url}#argus-microdata-{item.index}"
            observation_id = stable_observation_id(
                collection_id=collection_id,
                source_id=self.source_id,
                entity_type="structured_entity",
                entity_id=entity_id,
                source_url=page_url,
                content_hash=content_hash,
            )
            observation = Observation(
                observation_id=observation_id,
                collection_id=collection_id,
                analysis_id=request.analysis_id,
                consumer=request.consumer,
                source=self.source_id,
                source_kind="microdata",
                url=page_url,
                entity_type="structured_entity",
                entity_id=entity_id,
                title=item.title,
                text=item.text,
                data=item_data,
                published_at=item.published_at,
                content_hash=content_hash,
                provenance={
                    "snapshot_id": snapshot_id,
                    "page_url": page_url,
                    "item_index": item.index,
                    "research_goals": research_goals,
                    "extractor": extraction.extractor_version,
                    "extraction_truncated": extraction.truncated,
                    "item_truncated": item.truncated,
                    "itemref_items_skipped": extraction.itemref_skipped,
                    "remote_vocabularies_resolved": False,
                },
                quality={
                    "evidence_backed": True,
                    "machine_readable": True,
                    "source_declared": True,
                    "lossless": not item.truncated and not extraction.truncated,
                },
            )
            evidence_text = canonical[:10_000]
            evidence_item = Evidence(
                evidence_id=stable_evidence_id(
                    observation_id=observation.observation_id,
                    evidence_type="microdata",
                    source_url=page_url,
                    text=evidence_text,
                ),
                observation_id=observation.observation_id,
                type="microdata",
                text=evidence_text,
                source=EvidenceSource(
                    provider=self.source_id,
                    url=page_url,
                    collected_at=observation.collected_at,
                    source_id=self.source_id,
                ),
                metadata={
                    "snapshot_id": snapshot_id,
                    "item_index": item.index,
                    "research_goals": research_goals,
                    "extractor": extraction.extractor_version,
                    "canonical_sha256": content_hash,
                    "evidence_excerpt_truncated": len(canonical) > len(evidence_text),
                    "remote_vocabularies_resolved": False,
                },
            )
            observations.append(observation)
            evidence_items.append(evidence_item)
        return observations, evidence_items

    async def health(self) -> dict[str, object]:
        payload = dict(await super().health())
        payload["semantic_html_table_extraction"] = True
        payload["html_microdata_extraction"] = True
        return payload
