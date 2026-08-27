from argus.research.public_map_sources import PublicMapSourceResearchPlanner


def test_public_map_planner_exposes_checkpoint_target_source_count():
    planner = PublicMapSourceResearchPlanner(target_sources_per_intent=3)

    assert planner.target_sources_per_intent == 3
    assert planner.target_source_count == 3
