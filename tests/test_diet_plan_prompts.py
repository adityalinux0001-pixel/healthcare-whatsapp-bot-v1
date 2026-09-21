from app.llm.prompts import DIET_PLAN_REVISION_PROMPT, DIET_PLAN_PROMPT


def test_revision_prompt_is_not_built_from_the_fresh_generation_prompt():
    """Regression test for a real failure: DIET_PLAN_REVISION_PROMPT used to be
    DIET_PLAN_PROMPT + a short suffix, so every revision inherited "vary meal
    combinations" / "avoid unnecessary repetition" instructions meant for
    generating a brand-new day's plan. Combined with never being shown the
    plan it was supposedly revising, this produced a completely different
    plan on every single revision request, however small.
    """
    assert "current_plan_text" in DIET_PLAN_REVISION_PROMPT
    assert "vary meal combinations" not in DIET_PLAN_REVISION_PROMPT
    assert "avoid unnecessary repetition" not in DIET_PLAN_REVISION_PROMPT


def test_revision_prompt_renders_with_current_plan_and_instruction():
    rendered = DIET_PLAN_REVISION_PROMPT.format(
        profile={"goal": "muscle_gain", "diet_preference": "veg"},
        summary="No long-term preferences recorded yet.",
        day_number=2,
        knowledge_context="Verified nutrition guidance.",
        current_plan_text="Breakfast: Paneer Bhurji...\nExercise: 30 min walk, squats.",
        modification_instruction="User missed today's exercise; make it easier.",
    )
    assert "Paneer Bhurji" in rendered
    assert "missed today's exercise" in rendered
    assert "editing it, not creating a new day's plan" in rendered
    assert "does NOT apply here" in rendered


def test_fresh_generation_prompt_still_allows_variation():
    # The ordinary from-scratch prompt should be untouched by the revision fix.
    assert "vary meal combinations" in DIET_PLAN_PROMPT
