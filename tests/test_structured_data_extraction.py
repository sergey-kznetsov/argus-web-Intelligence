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
