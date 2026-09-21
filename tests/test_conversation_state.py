from app.llm.conversation_schemas import ConversationRoute
from app.llm.context import build_response_context
from app.llm.route_policy import normalize_conversation_route


def test_self_contained_request_drops_history_even_if_router_selected_related_turns():
    route = ConversationRoute(
        intent="general_health",
        dialogue_act="request",
        confidence=0.98,
        grounding_required=True,
        relevant_history_indices=[4, 5, 6],
    )
    profile = {
        "goal": "weight_gain",
        "diet_preference": "veg",
        "allergies": "None reported",
        "medical_conditions": "None reported",
    }
    history = [
        {"role": "user", "content": "can I eat pizza?"},
        {"role": "assistant", "content": "Yes, in moderation."},
        {"role": "user", "content": "can I eat rice?"},
        {"role": "assistant", "content": "Yes."},
        {"role": "user", "content": "what should I eat for breakfast?"},
        {"role": "assistant", "content": "Try poha."},
        {"role": "user", "content": "can I eat sweets?"},
    ]
    _, selected, _ = build_response_context(profile, history, None, route)
    assert selected == []


def test_follow_up_keeps_only_small_relevant_history():
    route = ConversationRoute(
        intent="general_health",
        dialogue_act="follow_up",
        confidence=0.98,
        grounding_required=True,
        relevant_history_indices=[0, 1, 2, 3],
    )
    profile = {
        "goal": "weight_gain",
        "diet_preference": "veg",
        "allergies": "None reported",
        "medical_conditions": "None reported",
    }
    history = [
        {"role": "user", "content": "old topic"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "can I eat rice?"},
        {"role": "assistant", "content": "Yes, rice can fit."},
    ]
    _, selected, _ = build_response_context(profile, history, None, route)
    assert selected == history[-2:]


def test_saved_plan_route_without_plan_reference_is_downgraded():
    route = ConversationRoute(
        intent="saved_plan_retrieval",
        dialogue_act="request",
        confidence=0.98,
        grounding_required=False,
        plan_reference="none",
    )
    normalized = normalize_conversation_route(route)
    assert normalized.intent == "general_health"
    assert normalized.grounding_required is True


def test_profile_recall_without_fields_is_not_executed():
    route = ConversationRoute(
        intent="profile_recall",
        dialogue_act="request",
        confidence=0.98,
        grounding_required=False,
    )
    normalized = normalize_conversation_route(route)
    assert normalized.intent == "clarification"
