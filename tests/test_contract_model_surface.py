from argus.contracts.models import (
    CollectionListPage,
    CollectionResultSummary,
    CollectionSummary,
    EvidencePage,
    ObservationPage,
    ResultDeliveryLimits,
)


def test_contract_model_surface_used_by_api_and_web_remains_available():
    assert CollectionSummary.model_fields["observation_count"].is_required()
    assert CollectionSummary.model_fields["evidence_count"].is_required()
    assert CollectionListPage.model_fields["items"].annotation == list[CollectionSummary]
    assert CollectionResultSummary.model_fields["delivery_limits"].annotation is ResultDeliveryLimits
    assert ObservationPage.model_fields["next_cursor"].default is None
    assert EvidencePage.model_fields["next_cursor"].default is None
