from argus.recipes.compiler import AgentRecipeCompiler


def test_compile_stable_id_actions():
    actions = [
        {
            "input_text": {"text": "Пушкинская 277"},
            "interacted_element": {
                "node_name": "input",
                "attributes": {"id": "search"},
            },
        },
        {
            "click_element_by_index": {"index": 7},
            "interacted_element": {
                "node_name": "button",
                "attributes": {"data-testid": "submit"},
            },
        },
        {"done": {"success": True, "text": "done"}, "interacted_element": None},
    ]
    steps = AgentRecipeCompiler().compile(actions)
    assert steps is not None
    assert [step.action for step in steps] == ["fill", "click"]
    assert steps[0].selector == 'input[id="search"]'
    assert steps[1].selector == 'button[data-testid="submit"]'


def test_unknown_required_action_rejects_recipe():
    assert AgentRecipeCompiler().compile([{"upload_file": {"path": "x"}}]) is None


def test_xpath_is_fallback_selector():
    actions = [
        {
            "click_element_by_index": {"index": 3},
            "interacted_element": {
                "node_name": "button",
                "attributes": {},
                "x_path": "html/body/main/button[1]",
            },
        }
    ]
    steps = AgentRecipeCompiler().compile(actions)
    assert steps and steps[0].selector == "xpath=//html/body/main/button[1]"
