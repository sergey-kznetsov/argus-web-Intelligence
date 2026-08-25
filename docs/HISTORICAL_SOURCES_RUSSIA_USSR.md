# Historical sources for Russia, Russian Empire and former USSR

Verified: 2026-08-25.

This catalogue defines priority public/free discovery targets for ARGUS `historical_context`. It does not grant permission to bypass access controls or copy restricted media. ARGUS may use a source as discovery-only when the underlying object cannot legally or technically be retrieved in the base public contour.

## Priority A — directly useful for address/place research

### PastVu

- URL: https://pastvu.com/
- Main value: geotagged historical photographs tied to map locations and dates.
- ARGUS use: search/discover historical photos around a location; retain source page, image reference, date/caption/author/coordinates when source-declared.
- Historical role: visual confirmation of buildings, streets, infrastructure and former place appearance.
- Retrieval policy: public web only; no access-control bypass.

### ЭтоМесто

- URLs: https://etomesto.ru/ and https://etomesto.com/
- Main value: old maps with geographic alignment and comparison against modern maps.
- Coverage explicitly includes Russia and many countries/territories of the former Soviet Union.
- Material includes imperial maps, Red Army maps, WWII aerial imagery, Soviet satellite city maps, USSR administrative/tourist/transport schemes and other historical layers.
- ARGUS use: coordinate/place-targeted historical map discovery; retain map title/year/source URL and publicly available image/tile/reference metadata where allowed.

### Retromap

- URL: https://retromap.ru/
- Main value: thousands of old maps, map comparison/overlay and geographic place search; also contains a historical document/image gallery.
- ARGUS use: place-targeted map discovery and historical layer references.

### Российский государственный архив кинофотодокументов — electronic photo catalogue

- URL: https://photo.rgakfd.ru/
- Search URL: https://photo.rgakfd.ru/search
- Main value: state photo archive catalogue with search dimensions including subjects, persons, place of shooting, author and years.
- ARGUS use: place/year/entity targeted photo discovery. Preserve archive identifier, title/annotation, shooting place, date, author and public preview/reference when available.

## Priority B — authoritative digital collections and documents

### Президентская библиотека имени Б. Н. Ельцина

- URL: https://www.prlib.ru/
- Key collection: https://www.prlib.ru/collections/467000
- Main value: archival documents, maps, plans, photographs, film chronicles, periodicals and books covering the Russian Empire, Soviet Russia and modern Russian Federation.
- The `Территория России` collection explicitly separates materials by historical period and geography and contains `Карты и планы` and `Изобразительные материалы`.
- ARGUS use: place/entity/date targeted discovery for maps, documents and visual material. Respect item-level access/copy restrictions.

### Национальная электронная библиотека (НЭБ)

- URL: https://rusneb.ru/
- Maps collection example: https://kp.rusneb.ru/item/thematic-groups/kartograficheskiy-obraz-mira/maps-17-19
- Main value: digitised historical books, atlases, maps and city plans from national library holdings.
- ARGUS use: address/city/old-name query expansion into historic atlases, plans and local descriptions.

### Federal archival search systems — Росархив

- URL: https://archives.gov.ru/search-systems-catalog.shtml
- Main value: directory of official electronic catalogues/search systems for Russian federal archives, including photo/film catalogues and archival fonds.
- ARGUS use: source discovery router. Follow only public catalogue/search endpoints; archive-specific adapters can be added after their public interface is verified.

### Runivers / Руниверс

- URL: https://runivers.ru/
- Legacy catalogue: https://old.runivers.ru/
- Main value: digitised historical books, document collections and high-resolution atlases/maps of the Russian Empire.
- ARGUS use: old geography, administrative structure, maps, regional descriptions, historical documents.

### Library of Congress — Prokudin-Gorskii collection

- Russian exhibition: https://www.loc.gov/exhibits/empire/empire-ru.html
- Main value: digitised colour photographic survey of the Russian Empire from the early 20th century, including architecture, transport, industry, settlements and daily life across a wide geography.
- ARGUS use: historical image discovery by place/entity. Prefer the Library of Congress item/canonical URLs and source-declared identifiers.

## Priority C — contextual historical corpus

### Центр «Прожито»

- URL: https://prozhito.org/
- Main value: diaries, letters and personal historical texts, particularly useful for Soviet-era local/social context.
- ARGUS use: secondary/contextual discovery when a place, organisation or event appears in searchable ego-documents. This source should not outrank direct archival/map/photo evidence for a location.

## Source selection rules

For `historical_context`, ARGUS should use several complementary source families rather than treating one archive as sufficient:

1. current factual web and map entities;
2. Wayback captures for known URLs;
3. georeferenced historical maps;
4. georeferenced/archive photographs;
5. official archival catalogues and digitised documents;
6. historical books/periodicals;
7. contextual personal/documentary corpora;
8. generic web discovery for local/regional archives not in this catalogue.

## Historical query expansion

Given a place such as `Ижевск, Пушкинская, 277`, targeted discovery should generate bounded variants similar to:

```text
site:pastvu.com "Пушкинская" Ижевск
site:etomesto.ru Ижевск Пушкинская
site:retromap.ru Ижевск Пушкинская
site:photo.rgakfd.ru Ижевск Пушкинская
site:prlib.ru Ижевск Пушкинская
site:rusneb.ru Ижевск Пушкинская
site:runivers.ru Ижевск Пушкинская
```

When an old entity/name is discovered, the same source families should be queried again with that historical label.

## Image evidence requirements

A historical image reference should be normalised separately from the surrounding page when possible.

Minimum fields:

```text
source_page_url
image_url or archive item URL
caption/title
archive/source id
source-declared date/date range
source-declared place/coordinates
source-declared author/collection
related entity/address
collected_at
snapshot_id/content hash/provenance
```

An image is evidence of what the archive/page explicitly describes. ARGUS must not infer exact address/date from visual appearance alone.

## Implementation status

This document is a product source catalogue, not a claim that every listed source already has a dedicated adapter.

Expected implementation order:

1. targeted domain-aware historical discovery using the catalogue;
2. generic factual extraction from reachable source pages;
3. first-class historical image-reference extraction;
4. dedicated adapters/recipes for sources whose search/navigation requires stable special handling;
5. standalone `argus probe` acceptance runs against real Russian addresses;
6. measure coverage gaps and add additional regional/federal archives based on real probe results.
