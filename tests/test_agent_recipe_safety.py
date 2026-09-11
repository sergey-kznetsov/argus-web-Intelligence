from argus.recipes.compiler import AgentRecipeCompiler


def test_compiler_rejects_login_and_state_changing_controls() -> None:
    compiler = AgentRecipeCompiler()

    assert (
        compiler.compile(
            [
                {
                    "click_element": {"index": 4},
                    "interacted_element": {
                        "node_name": "button",
                        "attributes": {"id": "submit-login", "type": "submit"},
                        "x_path": "//button[@id='submit-login']",
                    },
                }
            ]
        )
        is None
    )


def test_compiler_rejects_arbitrary_keyboard_shortcuts() -> None:
    compiler = AgentRecipeCompiler()
    action = {
        "press": {"selector": "#query", "keys": "Control+L"},
    }

    assert compiler.compile([action]) is None


def test_compiler_keeps_safe_public_navigation_action() -> None:
    compiler = AgentRecipeCompiler()
    steps = compiler.compile([{"click": {"selector": "button[data-testid=show-more]"}}])

    assert steps is not None
    assert [(step.action, step.selector) for step in steps] == [
        ("click", "button[data-testid=show-more]")
    ]


def test_compiler_rejects_ambiguous_multi_action_object() -> None:
    compiler = AgentRecipeCompiler()

    assert (
        compiler.compile(
            [{"click": {"selector": "#more"}, "go_to_url": {"url": "https://example.com"}}]
        )
        is None
    )
