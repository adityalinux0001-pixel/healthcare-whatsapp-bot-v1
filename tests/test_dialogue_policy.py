from app.llm.conversation_schemas import ConversationRoute
from app.llm.context import build_response_context
from app.llm.prompts import CONVERSATION_ROUTER_PROMPT, GENERAL_QA_SYSTEM_PROMPT
from app.llm.route_policy import normalize_conversation_route


def test_self_contained_request_cannot_carry_related_history_into_answer():
    route = ConversationRoute(
        intent="general_health",
        dialogue_act="request",
        confidence=0.98,
        grounding_required=True,
        relevant_history_indices=[0, 1],
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
    ]
    _, selected, _ = build_response_context(profile, history, None, route)
    assert selected == []


def test_follow_up_is_allowed_to_use_only_a_small_context_slice():
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


def test_saved_plan_route_without_reference_is_downgraded_to_general_health():
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


def test_profile_recall_route_without_fields_cannot_execute():
    route = ConversationRoute(
        intent="profile_recall",
        dialogue_act="request",
        confidence=0.98,
        grounding_required=False,
    )
    normalized = normalize_conversation_route(route)
    assert normalized.intent == "clarification"


def test_router_prompt_requires_accept_offer_for_yes_please_continuations():
    rendered = CONVERSATION_ROUTER_PROMPT.format(
        user_message="yes please",
        history=(
            "ASSISTANT: Would you like some ideas for nutrient-dense pizza and rice options?"
        ),
        summary="No long-term summary yet.",
        profile={"goal": "weight_gain", "diet_preference": "veg"},
    )
    assert 'dialogue_act="accept_offer"' in rendered
    assert "yes please" in rendered


def test_general_prompt_receives_dialogue_contract():
    rendered = GENERAL_QA_SYSTEM_PROMPT.format(
        today_date="2026-09-18",
        profile={"goal": "weight_gain", "diet_preference": "veg"},
        summary="No summary yet.",
        knowledge_context="Verified nutrition guidance.",
    )
    assert "self-contained user request is answered on its own merits" in rendered
    assert "accepts a concrete offer" in rendered
