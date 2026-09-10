# Discovery через Sitemap

ARGUS использует `site_discovery` только как внутренний navigation layer. `robots.txt` и Sitemap entries сами по себе не создают factual coverage. Выбранный destination должен пройти normal source adapter и дать Observation/Evidence.

## Scope

Sitemap navigation ограничена HTTP(S) URLs на исходном hostname. Request-level allowed/denied domains продолжают применяться к final page URLs. Fan-out Sitemap index, final URLs, collection page budget и index depth ограничены.

Missing/malformed/blocked/oversized Sitemap работает fail-open: optional navigation branch прекращается без превращения collection в factual source failure.

## Gzip Sitemap

Поддерживаются same-host `.xml.gz`/`.gz`, объявленные в `robots.txt` или Sitemap index. FAST сохраняет bounded response bytes; `site_discovery` распознаёт gzip по magic bytes/media type и распаковывает локально.

Распаковка выполняется bounded streaming zlib. Максимальный uncompressed Sitemap size = `ARGUS_MAX_RESPONSE_BYTES`. Payload, который превышает лимит после распаковки, отклоняется до XML parsing. Invalid/truncated/multi-member/trailing-data gzip игнорируется как невалидный navigation source.

Decompressed XML проходит `defusedxml`; DTD/entity expansion отключён.

Sitemap остаётся navigation metadata, а не Evidence.

## Recursion

Sitemap index может поставить в очередь только один дополнительный index level. Final pages, найденные через Sitemap, получают `disable_site_discovery=true`, поэтому они не запускают новый recursive robots/Sitemap traversal.

Это сохраняет bounded behavior даже для крупных или cyclic Sitemap graphs.
