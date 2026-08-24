import gzip

from argus.extraction.gzip_data import BoundedGzipExtractor


def test_extracts_single_gzip_member_within_limits():
    payload = b"name,value\nA,1\n"
    compressed = gzip.compress(payload)
    extractor = BoundedGzipExtractor(
        max_compressed_bytes=len(compressed),
        max_uncompressed_bytes=len(payload),
        input_chunk_bytes=3,
    )

    result = extractor.extract(compressed)

    assert result.error_code is None
    assert result.body == payload
    assert result.compressed_bytes == len(compressed)
    assert result.uncompressed_bytes == len(payload)


def test_rejects_uncompressed_output_over_limit():
    compressed = gzip.compress(b"x" * 1000)
    extractor = BoundedGzipExtractor(
        max_compressed_bytes=len(compressed),
        max_uncompressed_bytes=100,
        input_chunk_bytes=5,
    )

    result = extractor.extract(compressed)

    assert result.body is None
    assert result.error_code == "GZIP_UNCOMPRESSED_LIMIT_EXCEEDED"
    assert result.uncompressed_bytes > 100


def test_rejects_compressed_input_over_limit_before_decompression():
    compressed = gzip.compress(b"payload")
    extractor = BoundedGzipExtractor(
        max_compressed_bytes=len(compressed) - 1,
        max_uncompressed_bytes=100,
    )

    result = extractor.extract(compressed)

    assert result.body is None
    assert result.error_code == "GZIP_COMPRESSED_TOO_LARGE"


def test_rejects_truncated_gzip_member():
    compressed = gzip.compress(b"payload")[:-2]
    extractor = BoundedGzipExtractor(
        max_compressed_bytes=1000,
        max_uncompressed_bytes=1000,
        input_chunk_bytes=2,
    )

    result = extractor.extract(compressed)

    assert result.body is None
    assert result.error_code in {"GZIP_TRUNCATED", "GZIP_INVALID"}


def test_rejects_concatenated_gzip_members():
    compressed = gzip.compress(b"first") + gzip.compress(b"second")
    extractor = BoundedGzipExtractor(
        max_compressed_bytes=1000,
        max_uncompressed_bytes=1000,
        input_chunk_bytes=3,
    )

    result = extractor.extract(compressed)

    assert result.body is None
    assert result.error_code == "GZIP_TRAILING_DATA"


def test_rejects_non_gzip_bytes():
    extractor = BoundedGzipExtractor(
        max_compressed_bytes=1000,
        max_uncompressed_bytes=1000,
    )

    result = extractor.extract(b"not gzip")

    assert result.body is None
    assert result.error_code == "GZIP_INVALID"
