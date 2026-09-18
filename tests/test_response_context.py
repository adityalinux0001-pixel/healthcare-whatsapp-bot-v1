from app.llm.context import build_response_context
from app.llm.conversation_schemas import ConversationRoute


def _route(**overrides):
    data = {
        "intent": "general_health",
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
    assert len(h) == 4
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
