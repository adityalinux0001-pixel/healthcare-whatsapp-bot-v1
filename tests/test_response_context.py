from app.llm.prompts import GENERAL_QA_SYSTEM_PROMPT
from app.llm.context import build_response_context
from app.llm.conversation_schemas import ConversationRoute


def _route(**overrides):
    data = {
        "intent": "general_health",
        "dialogue_act": "request",
        "confidence": 0.98,
        "grounding_required": True,
        "response_profile_fields": ["city", "hair_wash_frequency", "sexually_active", "name"],
        "relevant_history_indices": [0, 1, 4, 7, 999],
        "use_long_term_memory": False,
    }
    data.update(overrides)
    return ConversationRoute(**data)


def test_response_context_minimizes_profile_and_history_and_never_sends_sensitive_field():
    profile = {
        "name": "User",
        "age": 28,
        "city": "Indore",
        "height_cm": 175,
        "weight_kg": 65,
        "hair_wash_frequency": "2_3_times_week",
        "water_hardness": "hard",
        "sugary_food_drink_intake": "moderate",
        "family_hair_loss": "yes",
        "family_hair_loss_relation": "father",
        "dairy_intake": "moderate",
        "sexually_active": "yes",
        "medical_conditions": "asthma",
    }
    history = [
        {"role": "user", "content": "what was my hair wash frequency?"},
        {"role": "assistant", "content": "You said 2–3 times a week."},
        {"role": "user", "content": "what was my city?"},
        {"role": "assistant", "content": "You said Indore."},
        {"role": "user", "content": "how often should I wash my hair?"},
        {"role": "assistant", "content": "It depends on your scalp and hair type."},
        {"role": "user", "content": "what is my age?"},
        {"role": "assistant", "content": "You are 28."},
    ]

    p, h, summary = build_response_context(profile, history, None, _route())

    assert "name" not in p
    assert "sexually_active" not in p
    assert p["city"] == "Indore"
    assert p["hair_wash_frequency"] == "2_3_times_week"
    assert p["medical_conditions"] == "asthma"
    assert h == []
    assert summary is None


def test_long_term_memory_is_opt_in():
    profile = {"hair_wash_frequency": "daily", "family_hair_loss": "no"}
    history = [{"role": "user", "content": "what about dandruff?"}]
    route = _route(response_profile_fields=["hair_wash_frequency"], relevant_history_indices=[], use_long_term_memory=True)
    p, h, summary = build_response_context(profile, history, "User has dandruff.", route)
    assert p["hair_wash_frequency"] == "daily"
    assert h == []
    assert summary == "User has dandruff."


def test_grounded_context_uses_hair_profile_and_only_saved_safety_fields():
    profile = {
        "age": 24,
        "city": "Pune",
        "hair_wash_frequency": "daily",
        "water_hardness": "not_sure",
        "dairy_intake": "moderate",
    }
    route = _route(response_profile_fields=[])
    p, _, _ = build_response_context(profile, [], None, route)
    assert p["age"] == 24
    assert p["city"] == "Pune"
    assert p["hair_wash_frequency"] == "daily"
    assert "medical_conditions" not in p
    assert "allergies" not in p


def test_correction_dialogue_act_keeps_immediately_preceding_turn():
    history = [
        {"role": "user", "content": "I missed the hair wash yesterday"},
        {"role": "assistant", "content": "You can resume your usual routine gently."},
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


def test_general_qa_prompt_is_hair_focused_and_uses_grounding_policy():
    rendered = GENERAL_QA_SYSTEM_PROMPT.format(
        today_date="2026-10-09",
        profile={"hair_wash_frequency": "daily"},
        summary="No summary yet.",
        knowledge_context="Verified dermatology guidance.",
    )
    assert "Hair & Scalp Assistant" in rendered
    assert "Do not diagnose" in rendered
    assert "NO_VERIFIED_CONTEXT_AVAILABLE" in rendered
    assert "Do not turn hair questions into weight-loss" in rendered
