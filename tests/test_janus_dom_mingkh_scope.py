from pathlib import Path


def test_janus_contract_documentation_names_dom_mingkh_as_factual_source() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "docs" / "JANUS_RESIDENTIAL_CONTRACT.md").read_text(encoding="utf-8")
    assert "dom.mingkh.ru" in text
    assert "only `residential_premises_count`" in text
