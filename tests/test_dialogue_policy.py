from app.llm.conversation_schemas import ConversationRoute
from app.llm.context import build_response_context
from app.llm.prompts import CONVERSATION_ROUTER_PROMPT, GENERAL_QA_SYSTEM_PROMPT
from app.llm.route_policy import normalize_conversation_route


def test_self_contained_request_cannot_carry_related_history_into_answer():
    route = ConversationRoute(
        intent="general_health", dialogue_act="request", confidence=0.98,
        grounding_required=True, relevant_history_indices=[0, 1],
    )
    profile = {"hair_wash_frequency": "daily", "water_hardness": "hard"}
    history = [
        {"role": "user", "content": "How often should I wash my hair?"},
        {"role": "assistant", "content": "It depends on your scalp and hair type."},
    ]
    _, selected, _ = build_response_context(profile, history, None, route)
    assert selected == []


def test_follow_up_is_allowed_to_use_only_a_small_context_slice():
    route = ConversationRoute(
        intent="general_health", dialogue_act="follow_up", confidence=0.98,
        grounding_required=True, relevant_history_indices=[0, 1, 2, 3],
    )
    profile = {"hair_wash_frequency": "daily"}
    history = [
        {"role": "user", "content": "old topic"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "My scalp is itchy."},
        {"role": "assistant", "content": "Is there flaking too?"},
    ]
    _, selected, _ = build_response_context(profile, history, None, route)
    assert selected == history[-2:]


def test_saved_hair_routine_route_without_reference_is_preserved_for_safe_lookup_fallback():
    route = ConversationRoute(
        intent="saved_plan_retrieval", dialogue_act="request", confidence=0.98,
        grounding_required=False, plan_reference="none",
    )
    normalized = normalize_conversation_route(route)
    assert normalized.intent == "saved_plan_retrieval"
    assert normalized.grounding_required is False


def test_profile_recall_route_without_fields_cannot_execute():
    route = ConversationRoute(
        intent="profile_recall", dialogue_act="request", confidence=0.98,
        grounding_required=False,
    )
    normalized = normalize_conversation_route(route)
    assert normalized.intent == "clarification"


def test_router_schema_explains_accept_offer_behavior():
    description = ConversationRoute.model_fields["dialogue_act"].description
    assert "accept_offer" in description
    assert "immediately preceding assistant turn" in description


def test_general_prompt_receives_hair_safety_contract():
    rendered = GENERAL_QA_SYSTEM_PROMPT.format(
        today_date="2026-10-09",
        profile={"hair_wash_frequency": "daily"},
        summary="No summary yet.",
        knowledge_context="Verified dermatology guidance.",
    )
    assert "self-contained request should be answered on its own merits" in rendered
    assert "Do not diagnose a specific alopecia/condition" in rendered
    assert "hair shedding" in rendered
