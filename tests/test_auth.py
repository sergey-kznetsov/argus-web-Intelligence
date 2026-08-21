from pathlib import Path

from argus.config import Settings
from argus.security.auth import ensure_token, write_new_token


def test_empty_token_file_is_replaced(tmp_path: Path):
    path = tmp_path / "token"
    path.write_text("", encoding="utf-8")
    settings = Settings(token_file=path, db_path=tmp_path / "db.sqlite")

    token = ensure_token(settings)

    assert len(token) >= 32
    assert path.read_text(encoding="utf-8").strip() == token


def test_valid_token_is_reused(tmp_path: Path):
    path = tmp_path / "token"
    original = write_new_token(path)
    settings = Settings(token_file=path, db_path=tmp_path / "db.sqlite")

    assert ensure_token(settings) == original
