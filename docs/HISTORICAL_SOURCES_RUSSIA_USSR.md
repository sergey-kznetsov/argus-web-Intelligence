# Исторические источники России, Российской империи и бывшего СССР

Проверено: 10 сентября 2026 года.

Этот каталог задаёт приоритетные публичные/бесплатные discovery targets для `historical_context`. Он не разрешает обход access control или копирование ограниченных media. Если underlying object нельзя корректно получить в публичном контуре, источник может использоваться только для discovery/reference.

## Приоритет A — прямое исследование места и адреса

### PastVu

- URL: `https://pastvu.com/`
- Основная ценность: геопривязанные исторические фотографии с местом и датой.
- Использование ARGUS: поиск исторических фотографий вокруг location; сохранение source page, image reference, date/caption/author/coordinates, когда они объявлены источником.
- Роль: визуальное подтверждение зданий, улиц, инфраструктуры и прежнего вида места.
- Политика: только публичный web, без обхода access control.

В текущем коде есть dedicated `pastvu_historical` adapter.

### ЭтоМесто

- URLs: `https://etomesto.ru/`, `https://etomesto.com/`
- Основная ценность: старые карты с геопривязкой и сравнением с современными картами.
- Coverage включает Россию и территории бывшего СССР: imperial maps, Red Army maps, WWII aerial imagery, советские городские/административные/туристические/транспортные карты и другие исторические layers.
- Использование ARGUS: targeted discovery по координатам/месту, сохранение title/year/source URL и доступных публичных references.

Dedicated adapter в текущем runtime не зарегистрирован; используется как catalog/discovery target через общие механизмы.

### Retromap

- URL: `https://retromap.ru/`
- Основная ценность: старые карты, overlay/comparison, geographic search, исторические документы/изображения.
- Использование: place-targeted discovery и references.
- Dedicated adapter сейчас не зарегистрирован.

### Российский государственный архив кинофотодокументов

- URL: `https://photo.rgakfd.ru/`
- Search: `https://photo.rgakfd.ru/search`
- Основная ценность: официальный каталог с subject/person/place/author/year dimensions.
- Использование: targeted photo discovery; сохранять archive identifier, title/annotation, place/date/author и public preview/reference при наличии.
- Dedicated adapter сейчас не зарегистрирован.

## Приоритет B — авторитетные цифровые коллекции и документы

### Президентская библиотека имени Б. Н. Ельцина

- URL: `https://www.prlib.ru/`
- Collection: `https://www.prlib.ru/collections/467000`
- Документы, карты, планы, фотографии, кинохроника, периодика и книги по Российской империи, СССР/РСФСР и современной РФ.
- Использование: targeted discovery по place/entity/date с соблюдением item-level restrictions.

### Национальная электронная библиотека

- URL: `https://rusneb.ru/`
- Ценность: оцифрованные книги, атласы, карты и планы городов.
- Использование: расширение запросов по city/address/old-name в исторические atlases/plans/descriptions.

### Росархив: федеральные поисковые системы

- URL: `https://archives.gov.ru/search-systems-catalog.shtml`
- Ценность: directory официальных electronic catalogues/search systems федеральных архивов.
- Использование: discovery router; archive-specific adapter добавляется только после проверки публичного interface.

### Runivers / Руниверс

- URL: `https://runivers.ru/`
- Legacy catalogue: `https://old.runivers.ru/`
- Ценность: historical books, document collections, atlases/maps Российской империи.

### Library of Congress — Prokudin-Gorskii

- Russian exhibition: `https://www.loc.gov/exhibits/empire/empire-ru.html`
- Ценность: оцифрованная цветная фотоколлекция Российской империи начала XX века.
- Использование: historical image discovery по place/entity с source-declared identifiers.

Для перечисленных Priority B источников dedicated adapters в текущем bootstrap не зарегистрированы; каталог задаёт discovery priority, а factual material проходит общие fetch/extraction contracts.

## Приоритет C — контекстный корпус

### Центр «Прожито»

- URL: `https://prozhito.org/`
- Ценность: дневники, письма и personal historical texts, особенно советского периода.
- Использование: secondary/contextual discovery, когда place/organization/event встречается в corpus.
- Не должен вытеснять прямые map/photo/archive evidence для location.

## Выбор источников

Для `historical_context` нужно комбинировать несколько families:

1. current factual web/map entities;
2. Wayback captures для известных URL;
3. georeferenced historical maps;
4. historical/archive photographs;
5. official archive catalogues/digitized documents;
6. historical books/periodicals;
7. contextual personal/documentary corpora;
8. generic web для региональных/local archives вне каталога.

## Query expansion

Для места вроде `Ижевск, Пушкинская, 277` каталог может использовать bounded запросы:

```text
site:pastvu.com "Пушкинская" Ижевск
site:etomesto.ru Ижевск Пушкинская
site:retromap.ru Ижевск Пушкинская
site:photo.rgakfd.ru Ижевск Пушкинская
site:prlib.ru Ижевск Пушкинская
site:rusneb.ru Ижевск Пушкинская
site:runivers.ru Ижевск Пушкинская
```

Найденное старое название/entity может стать новым hypothesis anchor, но факт появляется только после отдельного factual fetch.

## Дополнительный operator catalogue

Можно добавить public historical domains без изменения Python code:

```text
ARGUS_HISTORICAL_SOURCE_CATALOG_FILE=/path/to/historical-sources.json
```

Пример:

```json
{
  "sources": [
    {
      "source_id": "regional_archive",
      "domain": "archive.region.example",
      "kind": "archive_catalogues",
      "priority": 500,
      "visual": false,
      "query_suffix": "история документы"
    }
  ]
}
```

Operator catalogue ограничен 200 entries и 512 KiB. Domains должны быть public DNS/root HTTP(S) без credentials, ports, paths, query или fragments. Operator entries не заменяют code-reviewed built-in source по ID/domain.

Каталог — discovery metadata, не trust grant и не Evidence.

## Image evidence

Historical image reference по возможности нормализуется отдельно и сохраняет:

```text
source_page_url
image_url или archive item URL
caption/title
archive/source id
source-declared date/date range
source-declared place/coordinates
source-declared author/collection
related entity/address
collected_at
snapshot/content hash/provenance
```

ARGUS не выводит exact address/date из visual appearance без отдельной квалифицированной CV capability.

## Статус реализации

Этот файл — source catalogue, а не заявление о dedicated adapter для каждого сайта. Текущий код напрямую содержит PastVu и общие historical planners/Wayback paths; остальные catalog targets должны считаться discovery targets, пока отдельный adapter/recipe не подтверждён в runtime и тестах.
