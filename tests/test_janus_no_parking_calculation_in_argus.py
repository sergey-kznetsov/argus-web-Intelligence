from pathlib import Path


def test_janus_contract_files_do_not_implement_parking_potential_formula() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "argus"
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            root / "consumer_registry.py",
            root / "toolpacks.py",
            root / "research" / "residential_sources.py",
            root / "sources" / "mingkh_residential.py",
        ]
    ).casefold()
    forbidden = (
        "open_spaces",
        "parking_potential",
        "parking potential =",
        "0.8) * 10",
        "public_parking_capacity",
        "parking_score",
    )
    for marker in forbidden:
        assert marker not in text
