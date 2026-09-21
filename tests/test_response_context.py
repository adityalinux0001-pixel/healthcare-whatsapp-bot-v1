from app.llm.prompts import GENERAL_QA_SYSTEM_PROMPT
from app.llm.context import build_response_context
from app.llm.conversation_schemas import ConversationRoute


def _route(**overrides):
    data = {
        "intent": "general_health",
        "dialogue_act": "request",
        "confidence": 0.98,
        "grounding_required": True,
        "response_profile_fields": ["goal", "name", "age"],
        "relevant_history_indices": [0, 1, 4, 7, 999],
        "use_long_term_memory": False,
    }
    data.update(overrides)
    return ConversationRoute(**data)


def test_response_context_minimizes_profile_and_history():
    profile = {
        "name": "Adarsh",
        "age": 28,
        "gender": "male",
        "height_cm": 175,
        "weight_kg": 65,
        "goal": "muscle_gain",
        "diet_preference": "vegan",
        "allergies": "None reported",
        "medical_conditions": "None reported",
        "food_dislikes": "paneer",
    }
    history = [
        {"role": "user", "content": "what was my goal?"},
        {"role": "assistant", "content": "Your saved goal is muscle gain."},
        {"role": "user", "content": "what was my name?"},
        {"role": "assistant", "content": "I don't have that detail saved yet."},
        {"role": "user", "content": "can I eat rice during weight gain?"},
        {"role": "assistant", "content": "Yes, rice can fit into a muscle-gain diet."},
        {"role": "user", "content": "what is my age?"},
        {"role": "assistant", "content": "Your saved age is 28."},
    ]

    p, h, summary = build_response_context(profile, history, None, _route())

    assert "name" not in p
    assert p["goal"] == "muscle_gain"
    assert p["allergies"] == "None reported"
    assert p["medical_conditions"] == "None reported"
    assert h == []
    assert all(0 <= i < len(history) for i in range(len(history)))
    assert summary is None


def test_long_term_memory_is_opt_in():
    profile = {"goal": "muscle_gain", "allergies": "None reported", "medical_conditions": "None reported"}
    history = [{"role": "user", "content": "what about sweets?"}]
    route = _route(response_profile_fields=["goal"], relevant_history_indices=[], use_long_term_memory=True)
    p, h, summary = build_response_context(profile, history, "User prefers an earlier dinner.", route)
    assert p["goal"] == "muscle_gain"
    assert h == []
    assert summary == "User prefers an earlier dinner."


def test_grounded_health_context_includes_core_personalization_fields():
    profile = {
        "goal": "muscle_gain",
        "diet_preference": "veg",
        "allergies": "None reported",
        "medical_conditions": "None reported",
        "age": 24,
    }
    route = _route(response_profile_fields=[])
    p, _, _ = build_response_context(profile, [], None, route)
    assert p["goal"] == "muscle_gain"
    assert p["diet_preference"] == "veg"
    assert p["allergies"] == "None reported"
    assert p["medical_conditions"] == "None reported"
    assert "age" not in p


def test_correction_dialogue_act_keeps_immediately_preceding_turn():
    """Regression test for a real failure: the user asked "but when i told you
    to update my plan?" right after the assistant said "Done - I updated
    today's saved plan...". Because dialogue_act=correction was not in the
    history allowlist, the answer model got no history at all and denied any
    record of the update it had just made.

    A correction is definitionally about the immediately preceding turn, so
    it must be allowed to carry history just like follow_up/accept_offer.
    """
    history = [
        {"role": "user", "content": "I missed today's exercise it was a busy day!"},
        {"role": "assistant", "content": "Done \u2705 I updated today's saved plan based on your latest request."},
    ]
    route = _route(
        intent="general_conversation",
        dialogue_act="correction",
        grounding_required=False,
        response_profile_fields=[],
        relevant_history_indices=[0, 1],
    )
    _, h, _ = build_response_context({}, history, None, route)
    assert h == history


def test_general_qa_prompt_treats_supplied_profile_as_known():
    rendered = GENERAL_QA_SYSTEM_PROMPT.format(
        today_date="2026-09-18",
        profile={"goal": "muscle_gain", "diet_preference": "veg", "allergies": "None reported", "medical_conditions": "None reported"},
        summary="No summary yet.",
        knowledge_context="Verified nutrition guidance.",
    )
    assert "Never ask the user to repeat a profile field" in rendered
    assert "Do not ask broad questions" in rendered
    assert "silently use the supplied goal and diet preference" in rendered
