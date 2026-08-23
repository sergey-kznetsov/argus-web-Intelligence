from __future__ import annotations

import json
from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from argus.contracts.models import CollectionRequest, Evidence, EvidenceSource, Observation
from argus.crawler.agent.base import AgentBackend, AgentTask
from argus.crawler.browser.runtime import BrowserCrawlerRuntime
from argus.crawler.fast.runtime import FastCrawlerRuntime
from argus.crawler.models import FetchResult
from argus.extraction.jsonld import EmbeddedJsonLdExtractor, JsonLdExtraction
from argus.extraction.page_metadata import PageMetadataExtraction, extract_page_metadata
from argus.history.snapshots import SnapshotService, sha256_text
from argus.normalization.identity import stable_evidence_id, stable_observation_id
from argus.recipes.compiler import AgentRecipeCompiler
from argus.recipes.service import RecipeManager
from argus.security.urls import UnsafeUrlError
from argus.sources.base import SourceResult, SourceTask


class GenericWebAdapter:
    source_id = "generic_web"
    intents = {"*"}

    def __init__(
        self,
        fast: FastCrawlerRuntime,
        browser: BrowserCrawlerRuntime,
        snapshots: SnapshotService,
        recipes: RecipeManager | None = None,
        agent: AgentBackend | None = None,
        recipe_compiler: AgentRecipeCompiler | None = None,
        sitemap_discovery_enabled: bool = False,
        json_ld_extractor: EmbeddedJsonLdExtractor | None = None,
    ) -> None:
        self.fast = fast
        self.browser = browser
        self.snapshots = snapshots
        self.recipes = recipes
        self.agent = agent
        self.recipe_compiler = recipe_compiler or AgentRecipeCompiler()
        self.sitemap_discovery_enabled = sitemap_discovery_enabled
        self.json_ld_extractor = json_ld_extractor or EmbeddedJsonLdExtractor()

    async def discover(self, request: CollectionRequest) -> list[SourceTask]:
        return [
            SourceTask(
                source_id=self.source_id,
                goal=request.intents[0],
                url=str(url),
                depth=0,
                metadata={
                    "intents": list(request.intents),
                    "research_goals": list(request.intents),
                    "allowed_domains": list(request.constraints.allowed_domains),
                },
            )
            for url in request.constraints.seed_urls
        ]

    async def fetch(self, task: SourceTask) -> FetchResult:
        recipe_failed = False
        if self.recipes is not None:
            recipe = await self.recipes.get(task.url, task.goal)
            if recipe is not None:
                try:
                    result = await self.browser.fetch(task.url, recipe=recipe)
                    if not result.blocked:
                        await self.recipes.mark_success(recipe)
                    return result
                except UnsafeUrlError:
                    raise
                except Exception:
                    recipe_failed = True
                    await self.recipes.mark_failure(recipe)

        if recipe_failed and self.agent is not None:
            guided = await self._agent_guided_fetch(task)
            if guided is not None:
                return guided

        try:
            result = await self.fast.fetch(task.url)
            if result.blocked or self._needs_browser(result.text):
                return await self._browser_or_agent(task)
            return result
        except UnsafeUrlError:
            raise
        except Exception:
            return await self._browser_or_agent(task)

    async def _browser_or_agent(self, task: SourceTask) -> FetchResult:
        try:
            return await self.browser.fetch(task.url)
        except UnsafeUrlError:
            raise
        except Exception as browser_error:
            if self.agent is not None:
                guided = await self._agent_guided_fetch(task)
                if guided is not None:
                    return guided
            raise browser_error

    async def _agent_guided_fetch(self, task: SourceTask) -> FetchResult | None:
        if self.agent is None:
            return None
        goals = self._research_goals(task)
        goal_text = ", ".join(goals)
        agent_result = await self.agent.run(
            AgentTask(
                url=task.url,
                goal=goal_text,
                instruction=(
                    f"Find the public page or view needed for goals '{goal_text}'. Use public site "
                    "navigation, search, filters and expandable sections when needed."
                ),
                context={
                    "allowed_domains": task.metadata.get("allowed_domains", []),
                    "research_goals": goals,
                },
            )
        )
        if agent_result.blocked:
            return FetchResult(
                url=task.url,
                final_url=task.url,
                status_code=0,
                content_type=None,
                text="",
                blocked=True,
                runtime=f"agent:{self.agent.name}",
                metadata={"agent_error": agent_result.error},
            )
        if not agent_result.success:
            return None

        if self.recipes is not None and agent_result.actions:
            steps = self.recipe_compiler.compile(agent_result.actions)
            if steps:
                candidate = await self.recipes.candidate(task.url, task.goal, steps)
                try:
                    replayed = await self.browser.fetch(task.url, recipe=candidate)
                except UnsafeUrlError:
                    raise
                except Exception:
                    pass
                else:
                    if not replayed.blocked:
                        await self.recipes.mark_success(candidate)
                        replayed.metadata.update(
                            {
                                "agent_backend": self.agent.name,
                                "agent_compiled_recipe": True,
                            }
                        )
                        return replayed

        for visited in reversed(agent_result.visited_urls):
            if visited == task.url:
                continue
            try:
                fetched = await self.browser.fetch(visited)
            except UnsafeUrlError:
                raise
            except Exception:
                continue
            fetched.metadata.update(
                {
                    "agent_backend": self.agent.name,
                    "agent_guided": True,
                    "agent_origin_url": task.url,
                }
            )
            return fetched
        return None

    async def extract(self, task: SourceTask, fetched, request: CollectionRequest) -> SourceResult:
        if fetched.blocked:
            return SourceResult(observations=[], blocked=True)
        collection_id = str(task.metadata.get("collection_id", ""))
        snapshot = await self.snapshots.capture(
            self.source_id,
            fetched.final_url,
            fetched.text,
            fetched.content_type,
            collection_id=collection_id,
        )
        text = self._main_text(fetched.text, fetched.content_type)
        content_hash = sha256_text(text)
        research_goals = self._research_goals(task)
        json_ld = self.json_ld_extractor.extract(fetched.text, fetched.content_type)
        page_metadata = extract_page_metadata(
            fetched.text,
            content_type=fetched.content_type,
            base_url=fetched.final_url,
        )
        observation_id = stable_observation_id(
            collection_id=collection_id,
            source_id=self.source_id,
            entity_type="document",
            source_url=fetched.final_url,
            content_hash=content_hash,
        )
        data = {
            "runtime": fetched.runtime,
            "status_code": fetched.status_code,
            "research_goals": research_goals,
            "json_ld_summary": {
                "blocks_seen": json_ld.blocks_seen,
                "blocks_invalid": json_ld.blocks_invalid,
                "blocks_oversized": json_ld.blocks_oversized,
                "entities": len(json_ld.entities),
            },
            "page_metadata_summary": {
                "fields": len(page_metadata.fields),
                "truncated": page_metadata.truncated,
            },
        }
        if fetched.metadata:
            data["fetch_metadata"] = fetched.metadata
        provenance: dict[str, object] = {
            "snapshot_id": snapshot.snapshot_id,
            "research_goals": research_goals,
        }
        if "recipe_id" in fetched.metadata:
            provenance["recipe_id"] = fetched.metadata["recipe_id"]
            provenance["recipe_version"] = fetched.metadata.get("recipe_version")
        discovery_provider = task.metadata.get("discovery_provider")
        if discovery_provider:
            engines_raw = task.metadata.get("discovery_engines", [])
            engines = (
                [str(item) for item in engines_raw]
                if isinstance(engines_raw, list)
                else []
            )
            provenance["discovery"] = {
                "provider": str(discovery_provider),
                "engines": engines,
                "rank": task.metadata.get("discovery_rank"),
            }
        observation = Observation(
            observation_id=observation_id,
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="web_page",
            url=fetched.final_url,
            entity_type="document",
            title=fetched.title,
            text=text[:100_000],
            data=data,
            content_hash=content_hash,
            provenance=provenance,
            quality={"evidence_backed": True},
        )
        evidence_text = text[:10_000]
        evidence = Evidence(
            evidence_id=stable_evidence_id(
                observation_id=observation.observation_id,
                evidence_type="document",
                source_url=fetched.final_url,
                text=evidence_text,
            ),
            observation_id=observation.observation_id,
            type="document",
            text=evidence_text,
            source=EvidenceSource(
                provider=self.source_id,
                url=fetched.final_url,
                collected_at=observation.collected_at,
                source_id=self.source_id,
            ),
            metadata={"research_goals": research_goals},
        )
        observations = [observation]
        evidence_items = [evidence]
        structured_observations, structured_evidence = self._json_ld_observations(
            json_ld,
            collection_id=collection_id,
            request=request,
            source_url=fetched.final_url,
            snapshot_id=snapshot.snapshot_id,
            research_goals=research_goals,
        )
        observations.extend(structured_observations)
        evidence_items.extend(structured_evidence)
        metadata_observation, metadata_evidence = self._page_metadata_observation(
            page_metadata,
            collection_id=collection_id,
            request=request,
            source_url=fetched.final_url,
            snapshot_id=snapshot.snapshot_id,
            research_goals=research_goals,
        )
        if metadata_observation is not None and metadata_evidence is not None:
            observations.append(metadata_observation)
            evidence_items.append(metadata_evidence)
        discovered = self._discovered_tasks(task, fetched, request, observation.collection_id)
        return SourceResult(
            observations=observations,
            evidence=evidence_items,
            discovered_tasks=discovered,
        )

    def _json_ld_observations(
        self,
        extraction: JsonLdExtraction,
        *,
        collection_id: str,
        request: CollectionRequest,
        source_url: str,
        snapshot_id: str,
        research_goals: list[str],
    ) -> tuple[list[Observation], list[Evidence]]:
        observations: list[Observation] = []
        evidence_items: list[Evidence] = []
        for entity in extraction.entities:
            canonical = json.dumps(
                entity.data,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            content_hash = sha256_text(canonical)
            entity_id = self._json_ld_entity_id(
                entity.data,
                entity.block_index,
                entity.node_index,
                content_hash,
            )
            observation_id = stable_observation_id(
                collection_id=collection_id,
                source_id=self.source_id,
                entity_type="structured_entity",
                entity_id=entity_id,
                source_url=source_url,
                content_hash=content_hash,
            )
            provenance = {
                "snapshot_id": snapshot_id,
                "page_url": source_url,
                "research_goals": research_goals,
                "json_ld": {
                    "block_index": entity.block_index,
                    "node_index": entity.node_index,
                    "remote_contexts_resolved": False,
                },
            }
            observation = Observation(
                observation_id=observation_id,
                collection_id=collection_id,
                analysis_id=request.analysis_id,
                consumer=request.consumer,
                source=self.source_id,
                source_kind="json_ld",
                url=source_url,
                entity_type="structured_entity",
                entity_id=entity_id,
                title=self._json_ld_label(entity.data),
                text=self._json_ld_description(entity.data),
                data=entity.data,
                content_hash=content_hash,
                provenance=provenance,
                quality={"evidence_backed": True, "machine_readable": True},
            )
            evidence_text = canonical[:10_000]
            evidence = Evidence(
                evidence_id=stable_evidence_id(
                    observation_id=observation.observation_id,
                    evidence_type="json_ld",
                    source_url=source_url,
                    text=evidence_text,
                ),
                observation_id=observation.observation_id,
                type="json_ld",
                text=evidence_text,
                source=EvidenceSource(
                    provider=self.source_id,
                    url=source_url,
                    collected_at=observation.collected_at,
                    source_id=self.source_id,
                ),
                metadata={
                    "research_goals": research_goals,
                    "json_ld_block_index": entity.block_index,
                    "json_ld_node_index": entity.node_index,
                    "remote_contexts_resolved": False,
                },
            )
            observations.append(observation)
            evidence_items.append(evidence)
        return observations, evidence_items

    def _page_metadata_observation(
        self,
        extraction: PageMetadataExtraction,
        *,
        collection_id: str,
        request: CollectionRequest,
        source_url: str,
        snapshot_id: str,
        research_goals: list[str],
    ) -> tuple[Observation | None, Evidence | None]:
        if not extraction.fields:
            return None, None
        canonical = json.dumps(
            extraction.fields,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        content_hash = sha256_text(canonical)
        entity_id = extraction.canonical_url or source_url
        observation_id = stable_observation_id(
            collection_id=collection_id,
            source_id=self.source_id,
            entity_type="document_metadata",
            entity_id=entity_id,
            source_url=source_url,
            content_hash=content_hash,
        )
        title = self._metadata_string(
            extraction.fields,
            "og_title",
            "dcterms_title",
            "dc_title",
        )
        description = self._metadata_string(
            extraction.fields,
            "og_description",
            "description",
            "dcterms_description",
            "dc_description",
        )
        observation = Observation(
            observation_id=observation_id,
            collection_id=collection_id,
            analysis_id=request.analysis_id,
            consumer=request.consumer,
            source=self.source_id,
            source_kind="page_metadata",
            url=source_url,
            entity_type="document_metadata",
            entity_id=entity_id,
            title=title,
            text=description,
            data=extraction.fields,
            published_at=extraction.published_at,
            content_hash=content_hash,
            provenance={
                "snapshot_id": snapshot_id,
                "page_url": source_url,
                "canonical_url": extraction.canonical_url,
                "research_goals": research_goals,
                "extractor": extraction.extractor_version,
                "truncated_scan": extraction.truncated,
            },
            quality={
                "evidence_backed": True,
                "machine_readable": True,
                "source_declared": True,
            },
        )
        evidence_text = canonical[:10_000]
        evidence = Evidence(
            evidence_id=stable_evidence_id(
                observation_id=observation.observation_id,
                evidence_type="page_metadata",
                source_url=source_url,
                text=evidence_text,
            ),
            observation_id=observation.observation_id,
            type="page_metadata",
            text=evidence_text,
            source=EvidenceSource(
                provider=self.source_id,
                url=source_url,
                collected_at=observation.collected_at,
                source_id=self.source_id,
            ),
            metadata={
                "canonical_url": extraction.canonical_url,
                "research_goals": research_goals,
                "extractor": extraction.extractor_version,
            },
        )
        return observation, evidence

    @staticmethod
    def _metadata_string(fields: dict[str, object], *keys: str) -> str | None:
        for key in keys:
            value = fields.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    async def normalize(self, result: SourceResult) -> SourceResult:
        return result

    async def health(self) -> dict[str, object]:
        return {
            "source_id": self.source_id,
            "status": "ok",
            "agent_enabled": self.agent is not None,
            "recipes_enabled": self.recipes is not None,
            "sitemap_discovery_enabled": self.sitemap_discovery_enabled,
            "json_ld_extraction": True,
            "page_metadata_extraction": True,
        }

    def _discovered_tasks(
        self,
        task: SourceTask,
        fetched,
        request: CollectionRequest,
        collection_id: str,
    ) -> list[SourceTask]:
        discovered: list[SourceTask] = []
        seen: set[str] = set()
        max_depth = request.constraints.max_depth
        allowed = {d.lower().strip(".") for d in request.constraints.allowed_domains}
        denied = {d.lower().strip(".") for d in request.constraints.denied_domains}
        parsed_final = urlparse(fetched.final_url)
        seed_host = (parsed_final.hostname or "").lower().strip(".")
        research_goals = self._research_goals(task)

        for feed_url in self._feed_links(fetched.text, fetched.final_url, fetched.content_type):
            if self._domain_allowed(feed_url, seed_host, allowed, denied):
                key = f"rss_atom:{feed_url}"
                if key not in seen:
                    seen.add(key)
                    discovered.append(
                        SourceTask(
                            source_id="rss_atom",
                            goal=task.goal,
                            url=feed_url,
                            depth=task.depth,
                            metadata={
                                "collection_id": collection_id,
                                "discovered_from": fetched.final_url,
                                "research_goals": research_goals,
                            },
                        )
                    )

        for feed_url in self._json_feed_links(
            fetched.text,
            fetched.final_url,
            fetched.content_type,
        ):
            if self._domain_allowed(feed_url, seed_host, allowed, denied):
                key = f"json_feed:{feed_url}"
                if key not in seen:
                    seen.add(key)
                    discovered.append(
                        SourceTask(
                            source_id="json_feed",
                            goal=task.goal,
                            url=feed_url,
                            depth=task.depth,
                            metadata={
                                "collection_id": collection_id,
                                "discovered_from": fetched.final_url,
                                "research_goals": research_goals,
                            },
                        )
                    )

        if task.depth < max_depth:
            for link in fetched.links[: request.constraints.max_pages]:
                link = urldefrag(link)[0]
                if not link or not self._domain_allowed(link, seed_host, allowed, denied):
                    continue
                key = f"{self.source_id}:{link}"
                if key in seen:
                    continue
                seen.add(key)
                discovered.append(
                    SourceTask(
                        source_id=self.source_id,
                        goal=task.goal,
                        url=link,
                        depth=task.depth + 1,
                        metadata={
                            "collection_id": collection_id,
                            "allowed_domains": list(request.constraints.allowed_domains),
                            "research_goals": research_goals,
                        },
                    )
                )

            site_task = self._site_discovery_task(
                task,
                fetched.final_url,
                fetched.content_type,
                collection_id,
                request,
            )
            if site_task is not None and site_task.dedupe_key not in seen:
                discovered.append(site_task)
        return discovered

    def _site_discovery_task(
        self,
        task: SourceTask,
        final_url: str,
        content_type: str | None,
        collection_id: str,
        request: CollectionRequest,
    ) -> SourceTask | None:
        if not self.sitemap_discovery_enabled:
            return None
        if task.metadata.get("disable_site_discovery") or task.metadata.get("archive_original_url"):
            return None
        if content_type and "html" not in content_type.casefold():
            return None
        parsed = urlparse(final_url)
        host = (parsed.hostname or "").lower().strip(".")
        if parsed.scheme not in {"http", "https"} or not host or not parsed.netloc:
            return None
        origin = f"{parsed.scheme}://{parsed.netloc}"
        robots_url = origin + "/robots.txt"
        return SourceTask(
            source_id="site_discovery",
            goal=task.goal,
            url=robots_url,
            depth=task.depth,
            task_key=f"site_discovery:robots:{origin}",
            metadata={
                "collection_id": collection_id,
                "site_discovery_kind": "robots",
                "root_host": host,
                "root_origin": origin,
                "discovered_from": final_url,
                "allowed_domains": list(request.constraints.allowed_domains),
                "research_goals": self._research_goals(task),
            },
        )

    @staticmethod
    def _research_goals(task: SourceTask) -> list[str]:
        raw = task.metadata.get("research_goals", [])
        goals: list[str] = []
        if isinstance(raw, list):
            for value in raw:
                goal = str(value).strip()
                if goal and goal not in goals:
                    goals.append(goal)
        if not goals and task.goal:
            goals.append(task.goal)
        return goals

    @staticmethod
    def _json_ld_entity_id(
        data: dict[str, object],
        block_index: int,
        node_index: int,
        content_hash: str,
    ) -> str:
        for key in ("@id", "url"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:2_000]
        return f"jsonld:{block_index}:{node_index}:{content_hash[:24]}"

    @staticmethod
    def _json_ld_label(data: dict[str, object]) -> str | None:
        for key in ("name", "headline"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:1_000]
        return None

    @staticmethod
    def _json_ld_description(data: dict[str, object]) -> str | None:
        value = data.get("description")
        if isinstance(value, str) and value.strip():
            return value.strip()[:100_000]
        return None

    @staticmethod
    def _domain_allowed(url: str, seed_host: str, allowed: set[str], denied: set[str]) -> bool:
        domain = (urlparse(url).hostname or "").lower().strip(".")
        if not domain:
            return False
        if any(domain == d or domain.endswith("." + d) for d in denied):
            return False
        if allowed:
            return any(domain == d or domain.endswith("." + d) for d in allowed)
        return domain == seed_host

    @staticmethod
    def _feed_links(html: str, base_url: str, content_type: str | None) -> list[str]:
        if content_type and "html" not in content_type.lower():
            return []
        soup = BeautifulSoup(html, "html.parser")
        links: list[str] = []
        seen: set[str] = set()
        for tag in soup.find_all("link", href=True):
            rel = {str(item).lower() for item in tag.get("rel", [])}
            mime = str(tag.get("type", "")).lower()
            if "alternate" not in rel or mime not in {
                "application/rss+xml",
                "application/atom+xml",
            }:
                continue
            url = urldefrag(urljoin(base_url, tag["href"]))[0]
            if url.startswith(("http://", "https://")) and url not in seen:
                seen.add(url)
                links.append(url)
        return links

    @staticmethod
    def _json_feed_links(html: str, base_url: str, content_type: str | None) -> list[str]:
        if content_type and "html" not in content_type.lower():
            return []
        soup = BeautifulSoup(html, "html.parser")
        links: list[str] = []
        seen: set[str] = set()
        for tag in soup.find_all("link", href=True):
            rel = {str(item).lower() for item in tag.get("rel", [])}
            mime = str(tag.get("type", "")).split(";", 1)[0].strip().lower()
            if "alternate" not in rel or mime != "application/feed+json":
                continue
            url = urldefrag(urljoin(base_url, tag["href"]))[0]
            if url.startswith(("http://", "https://")) and url not in seen:
                seen.add(url)
                links.append(url)
        return links

    @staticmethod
    def _needs_browser(html: str) -> bool:
        sample = html[:100_000].lower()
        return (
            "__next_data__" in sample
            or 'id="root"></div>' in sample
            or "enable javascript" in sample
        )

    @staticmethod
    def _main_text(content: str, content_type: str | None) -> str:
        if content_type and "html" not in content_type.lower():
            return content
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        return "\n".join(
            line.strip() for line in soup.get_text("\n").splitlines() if line.strip()
        )
