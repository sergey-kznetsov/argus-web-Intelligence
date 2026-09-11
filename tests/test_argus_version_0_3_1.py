import tomllib
from pathlib import Path

from argus import __version__


def test_project_and_runtime_versions_match() -> None:
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"] == __version__ == "0.3.1"
