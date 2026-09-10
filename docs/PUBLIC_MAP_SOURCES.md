# Публичные карты в ARGUS

ARGUS может использовать Яндекс Карты, 2ГИС и Google Maps как публичные web/navigation источники. Это не обязательные платные API и не отдельный factual authority.

## Текущая роль в Kraken urban_signals

Старое описание, где ARGUS должен был извлекать отзывы с публичных карт через AGENT, больше не соответствует runtime.

Для текущего Kraken Tool Pack:

- `review` не входит в allowed factual types;
- establishment reviews не должны становиться предметом Kraken;
- public-map observations, помеченные `information_only`, не выдаются consumer'у как обычные Observation;
- связанный Evidence сохраняется и отвязывается от подавленного Observation для проверяемости контекста;
- consumer-delivery filtering не пытается семантически решать, является ли произвольный текст жалобой/инцидентом.

Эта граница реализована в `ConsumerDeliveryProjector` (`consumer-delivery/2`).

## Mandatory public-map lanes

Для `urban_signals` public-map providers входят в обязательный последовательный research contour после source contours.

Поддерживаемые provider IDs:

```text
yandex_maps_web
2gis_web
google_maps_web
```

Mandatory planner строит street anchors по территории и проходит **каждую улицу, попавшую в radius**, по каждому provider. Это исправляет старую модель исследования только исходного адреса.

Состояние сохраняется в checkpoint, включая expected/attempted/processed street anchors и `street_scope_complete`. Итоговая диагностика входит в `research_lane_coverage`.

## Evidence boundary

Сам URL карты, открытая карточка, найденное место или navigation metadata не доказывают предметный факт. Information-only map context не должен подменять сообщения жителей.

Search/query/map navigation используется для:

- уточнения spatial context;
- формирования street/entity anchors;
- проверки доступности public map source;
- дальнейшей навигации по другим публичным источникам, если это предусмотрено planner policy.

## AGENT

Код semantic AGENT/public-review escalation в репозитории существует исторически, но текущий `build_services()` не передаёт AGENT в web adapter. Поэтому AGENT rounds и agent-generated review navigation сейчас не являются фактически работающим production path.

Если AGENT будет возвращён, любой route по-прежнему должен проходить deterministic SiteRecipe/BROWSER verification и не может обходить CAPTCHA/access control.

## Free contour

ARGUS не требует Yandex Maps API, 2GIS API или Google Places API. Нельзя превращать наблюдаемые private/internal endpoints frontend-приложений в неофициальный стабильный API contract.

При изменении публичного интерфейса система должна использовать публичную навигацию и общие browser/source contracts, а не зависеть от скрытых endpoint'ов сайта.
