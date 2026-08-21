from argus.sources.registry import SourceRegistry


class Source:
    source_id = "x"
    intents = {"reviews"}


def test_registry_filters_intents():
    registry = SourceRegistry()
    registry.register(Source())
    assert len(registry.for_intents(["reviews"])) == 1
    assert len(registry.for_intents(["local_news"])) == 0
