# Сжатые структурированные документы gzip

ARGUS поддерживает публичные структурированные файлы, явно опубликованные в gzip, без использования неограниченной распаковки.

Поддерживаются:

```text
.csv.gz
.tsv.gz
.tab.gz
.json.gz
.geojson.gz
.xml.gz
```

Gzip здесь — транспортный/контейнерный слой. После bounded распаковки содержимое проходит через существующий ограниченный parser CSV/TSV/JSON/XML.

```text
public *.csv.gz / *.json.gz / *.xml.gz
  -> bounded FAST body
  -> single-member streaming gzip decompression
  -> existing bounded structured-data parser
  -> Observation + Evidence + Snapshot
```

## Отличие от HTTP Content-Encoding

Этот путь относится к явно опубликованному gzip-файлу. Обычный HTTP `Content-Encoding: gzip` не считается отдельным document format: транспортное декодирование остаётся обязанностью HTTP client/runtime.

URL должен содержать известное structured расширение перед `.gz`. Adapter также требует либо gzip magic bytes, либо соответствующий gzip/octet-stream media type. Поэтому HTML error page, возвращённая по URL `*.csv.gz`, не должна ошибочно стать dataset.

## Граница распаковки

ARGUS использует `zlib.decompressobj(16 + MAX_WBITS)` и ограничивает каждый вызов оставшимся допустимым объёмом output + один байт.

Не используются неограниченные `gzip.decompress()` или `zlib.decompress()` для публичных artifacts.

Compressed input и uncompressed output ограничены независимо. По умолчанию оба лимита используют `structured_data_max_bytes`, поэтому маленький архив не может распаковаться в произвольно большой payload.

## Только один gzip member

Версия 1 принимает ровно один gzip member.

Concatenated members и trailing bytes отклоняются с `GZIP_TRAILING_DATA` и не объединяются молча. Один source URL должен соответствовать одному детерминированному structured payload.

## Ошибки

Gzip layer может вернуть:

```text
GZIP_COMPRESSED_TOO_LARGE
GZIP_UNCOMPRESSED_LIMIT_EXCEEDED
GZIP_INVALID
GZIP_TRUNCATED
GZIP_TRAILING_DATA
GZIP_BINARY_UNAVAILABLE
```

Если полученный gzip artifact невозможно распаковать, ARGUS сохраняет evidence факта получения файла: compressed SHA-256, размер и structured error. Такой файл не отправляется повторно через Playwright.

## Identity и provenance

Source identity строится по исходным compressed bytes.

`Observation.content_hash`, `binary_sha256` и stable document Observation ID основаны на compressed artifact, а не на распакованном payload.

Compression metadata содержит:

- format `gzip`;
- `single_member_required=true`;
- compressed/uncompressed byte count;
- compressed SHA-256;
- uncompressed SHA-256 при успешной распаковке;
- logical inner URL без `.gz`;
- extractor version;
- gzip error code при наличии.

Metadata сохраняется в Observation data/provenance и Evidence metadata.

## Внутренний parser

После успешной распаковки bytes передаются существующему bounded structured-data extractor с logical inner filename. Gzip media type не используется как media type внутреннего документа.

Следовательно:

- JSON сохраняет текущую UTF-8 policy;
- XML идёт через hardened `defusedxml` path и XML limits;
- CSV/TSV используют deterministic encoding policy и record/column/cell limits.

Parser не получает сетевого доступа.

## Runtime limits

Новые environment variables не вводятся. Gzip использует `structured_data_max_bytes` как основу лимитов compressed и uncompressed bytes.

## Не входит в текущий контракт

Версия 1 не:

- распаковывает `.gz.gz` рекурсивно;
- обрабатывает `.zip`, `.7z`, `.rar` и tar через этот path;
- принимает concatenated gzip members;
- угадывает inner format по произвольным compressed bytes;
- принимает consumer-specific решения Kraken/Janus.
