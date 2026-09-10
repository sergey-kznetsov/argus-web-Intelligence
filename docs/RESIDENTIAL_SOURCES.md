# Данные жилых домов

ARGUS поддерживает source-scoped factual intents для жилого фонда:

```text
residential_population
residential_premises_count
```

Первый означает число жителей, прямо опубликованное источником; второй — число квартир/жилых помещений, прямо опубликованное источником.

## Источник

Текущий dedicated adapter — `mingkh_residential` для публичного web-интерфейса `dom.mingkh.ru`.

Для этих intents ARGUS не должен молча подменять источник другим housing-сайтом и не должен выводить число жителей из числа квартир, площади или среднего размера домохозяйства.

## Navigation contract

При наличии building address curated planner использует source-owned navigation через `robots.txt`/Sitemap и может дополнительно использовать discovery для поиска публичной detail page. Sitemap/search results остаются навигацией, а не Evidence.

Planner не должен использовать запрещённый source `robots.txt` путь `/search` для обхода published crawl policy.

Запрос без building address не превращается в произвольный city-wide выбор дома.

`site_discovery` может использоваться как явно запланированная часть source contract даже если opportunistic sitemap expansion для Generic Web выключен.

## Evidence contract

Residential fact создаётся только когда:

1. final fetched URL остаётся на `dom.mingkh.ru`;
2. страница проходит deterministic territory/address relevance;
3. значение опубликовано рядом с распознаваемой source label;
4. значение однозначно разбирается как non-negative integer;
5. сохраняются Snapshot и label/value Evidence.

Поддерживаемые labels для помещений включают:

```text
Количество квартир
Количество жилых помещений
Жилых помещений
```

Для жителей:

```text
Количество жителей
Численность жителей
Число жителей
```

Разные значения одного показателя на странице дают `MINGKH_RESIDENTIAL_VALUE_CONFLICT`, а не произвольный выбор.

## Текущее ограничение guided navigation

В коде есть контракт SiteRecipe/AGENT-guided navigation для сложного интерфейса `dom.mingkh.ru`, но текущий production `build_services()` создаёт `AtomicContentWebAdapter` с `agent=None`. Следовательно, фактический runtime сейчас опирается на доступные deterministic/public navigation paths и BROWSER, а не на OllamaRecipeAgent.

Эту возможность нельзя считать рабочей, пока AGENT снова не подключён и не подтверждён E2E.

## CAPTCHA и access challenges

`не робот`, captcha, access-denied и аналогичные страницы считаются блокировкой. ARGUS возвращает `MINGKH_ACCESS_CHALLENGE`/blocked state и не решает challenge автоматически.

## Provenance

Успешные значения сохраняются как `residential_building_fact` с intent, integer value, source label и `estimated=false`. Publication/collection/source metadata и Snapshot сохраняются в provenance.

## Consumer boundary

Наличие `mingkh_residential` в общем ARGUS registry не означает, что он разрешён каждому consumer. Текущий Kraken `urban_signals` Tool Pack этот adapter не включает. Janus должен получить собственный profile/tool pack после фиксации его реального contract.
