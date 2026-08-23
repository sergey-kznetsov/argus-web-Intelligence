from argus.extraction.structured_data import BoundedStructuredDataExtractor


def extractor(**overrides):
    defaults = {
        "max_bytes": 1024 * 1024,
        "max_records": 10,
        "max_columns": 10,
        "max_cell_chars": 100,
        "max_json_depth": 10,
        "max_json_nodes": 100,
    }
    defaults.update(overrides)
    return BoundedStructuredDataExtractor(**defaults)


def test_semicolon_csv_becomes_named_records():
    result = extractor().extract(
        "name;value\nДом;12\nШкола;3\n".encode(),
        content_type="text/csv; charset=utf-8",
        url="https://example.com/data.csv",
    )
    assert result.error_code is None
    assert result.document_type == "csv"
    assert result.delimiter == ";"
    assert result.has_header is True
    assert result.payload == {
        "columns": ["name", "value"],
        "records": [
            {"name": "Дом", "value": "12"},
            {"name": "Школа", "value": "3"},
        ],
    }


def test_cp1251_csv_without_charset_uses_bounded_legacy_fallback():
    body = "название;значение\nШкола;3\n".encode("cp1251")

    result = extractor().extract(
        body,
        content_type="text/csv",
        url="https://example.com/data.csv",
    )

    assert result.error_code is None
    assert result.encoding == "cp1251"
    assert result.payload["records"][0] == {"название": "Школа", "значение": "3"}


def test_utf16_tsv_bom_takes_precedence_over_missing_charset():
    body = "name\tvalue\nДом\t12\n".encode("utf-16")

    result = extractor().extract(
        body,
        content_type="text/tab-separated-values",
        url="https://example.com/data.tsv",
    )

    assert result.error_code is None
    assert result.encoding == "utf-16"
    assert result.payload["records"][0] == {"name": "Дом", "value": "12"}


def test_json_payload_is_preserved_without_remote_work():
    result = extractor().extract(
        b'{"name":"site","count":2}',
        content_type="application/json",
        url="https://example.com/api/data",
    )
    assert result.error_code is None
    assert result.document_type == "json"
    assert result.payload == {"name": "site", "count": 2}
    assert result.column_count == 2


def test_json_uses_utf8_even_when_server_declares_legacy_charset():
    body = '{"город":"Ижевск"}'.encode("utf-8")

    result = extractor().extract(
        body,
        content_type="application/json; charset=cp1251",
        url="https://example.com/data.json",
    )

    assert result.error_code is None
    assert result.encoding == "utf-8"
    assert result.payload == {"город": "Ижевск"}


def test_non_utf8_network_json_is_rejected_instead_of_guessed():
    body = '{"город":"Ижевск"}'.encode("cp1251")

    result = extractor().extract(
        body,
        content_type="application/json",
        url="https://example.com/data.json",
    )

    assert result.payload is None
    assert result.error_code == "STRUCTURED_DATA_DECODE_ERROR"


def test_json_container_limit_fails_explicitly_instead_of_truncating():
    result = extractor(max_records=2).extract(
        b'[1,2,3]',
        content_type="application/json",
        url="https://example.com/data.json",
    )
    assert result.payload is None
    assert result.error_code == "STRUCTURED_DATA_LIMIT_EXCEEDED"
    assert result.truncated is False


def test_csv_record_limit_marks_normalized_payload_partial():
    result = extractor(max_records=2).extract(
        b"name,value\na,1\nb,2\nc,3\n",
        content_type="text/csv",
        url="https://example.com/data.csv",
    )
    assert result.error_code is None
    assert result.truncated is True
    assert result.rows_extracted == 2
    assert len(result.payload["records"]) == 2


def test_pathological_json_nesting_becomes_explicit_limit_error():
    body = ("[" * 1500 + "0" + "]" * 1500).encode()

    result = extractor(max_bytes=20_000).extract(
        body,
        content_type="application/json",
        url="https://example.com/deep.json",
    )

    assert result.payload is None
    assert result.error_code == "STRUCTURED_DATA_LIMIT_EXCEEDED"
