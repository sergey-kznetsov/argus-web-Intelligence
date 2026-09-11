from pathlib import Path


def test_janus_contract_files_do_not_implement_parking_potential_formula() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "argus"
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [root / "consumer_registry.py", root / "toolpacks.py"]
    )
    assert "open_spaces" not in text
    assert "parking_potential" not in text
