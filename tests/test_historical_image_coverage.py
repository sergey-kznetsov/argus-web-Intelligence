from argus.contracts.models import Observation
from argus.research.intent_coverage import IntentCoverageEvaluator


def image_observation(*, archive: bool = False, historical_image: bool = False) -> Observation:
    provenance = {}
    if archive:
        provenance["archive"] = {"historical_capture": True}
    if historical_image:
        provenance["historical_image"] = True
    return Observation(
        observation_id="historical-image-test",
        collection_id="historical-image-collection",
        analysis_id="historical-image-analysis",
        consumer="historical-image-consumer",
        source="generic_web",
        source_kind="image_reference",
        url="https://images.example.org/photo.jpg",
        entity_type="image",
        entity_id="https://images.example.org/photo.jpg",
        content_hash="a" * 64,
        provenance=provenance,
        quality={"source_declared_image": True},
    )


def test_generic_page_asset_counts_as_image_but_not_historical_image():
    evaluator = IntentCoverageEvaluator()
    observation = image_observation()

    assert evaluator.supports(observation, "images") is True
    assert evaluator.supports(observation, "historical_images") is False


def test_archived_image_reference_counts_as_historical_image():
    evaluator = IntentCoverageEvaluator()

    assert evaluator.supports(
        image_observation(archive=True),
        "historical_images",
    ) is True


def test_explicit_historical_image_provenance_counts_as_historical_image():
    evaluator = IntentCoverageEvaluator()

    assert evaluator.supports(
        image_observation(historical_image=True),
        "historical_images",
    ) is True


def test_explicit_intent_evidence_still_overrides_structural_fallback():
    evaluator = IntentCoverageEvaluator()
    observation = image_observation()
    observation.quality["intent_evidence"] = {"historical_images": True}

    assert evaluator.supports(observation, "historical_images") is True
