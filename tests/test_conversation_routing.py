from app.llm.conversation_schemas import ConversationRoute
from app.llm.prompts import CONVERSATION_ROUTER_PROMPT
from app.llm.route_policy import normalize_conversation_route


def route_for(**overrides):
    data = {"intent": "general_health", "confidence": 0.98, "grounding_required": True}
    data.update(overrides)
    return ConversationRoute.model_validate(data)


def test_hair_question_is_a_grounded_health_route():
    route = route_for()
    assert route.intent == "general_health"
    assert route.grounding_required is True
    assert route.profile_updates == []


def test_saved_hair_care_plan_request_is_preserved_for_deterministic_lookup():
    route = route_for(
        intent="saved_plan_retrieval", grounding_required=False,
        plan_reference="day_number", plan_day_number=2, plan_scope="full",
    )
    normalized = normalize_conversation_route(route)
    assert normalized.intent == "saved_plan_retrieval"
    assert normalized.grounding_required is False
    assert normalized.plan_day_number == 2
    assert normalized.plan_scope == "full"


def test_profile_recall_route_contains_only_requested_fields():
    route = route_for(intent="profile_recall", grounding_required=False, profile_fields=["hair_wash_frequency"])
    assert route.profile_fields == ["hair_wash_frequency"]
    assert route.profile_updates == []


def test_profile_update_requires_current_turn_evidence_in_contract():
    route = route_for(
        intent="profile_update",
        profile_updates=[{
            "field": "hair_wash_frequency", "value": "daily",
            "evidence": "I wash my hair daily", "confidence": 0.97,
        }],
    )
    assert route.profile_updates[0].evidence
    assert route.profile_updates[0].confidence >= 0.90


def test_hair_care_plan_modification_is_preserved_for_deterministic_revision():
    route = route_for(
        intent="plan_modification", grounding_required=False,
        plan_scope="full", modification_instruction="change dinner",
    )
    normalized = normalize_conversation_route(route)
    assert normalized.intent == "plan_modification"
    assert normalized.modification_instruction == "change dinner"


def test_router_prompt_is_about_hair_and_scalp_not_diet_plan_retrieval():
    rendered = CONVERSATION_ROUTER_PROMPT.format(
        user_message="How can I reduce hair breakage?",
        history="No recent conversation.",
        summary="No long-term summary yet.",
        profile={"hair_wash_frequency": "2_3_times_week", "water_hardness": "hard"},
    )
    assert "CURRENT SAVED HAIR PROFILE" in rendered
    assert "Do not diagnose" in rendered
    assert "Legacy daily diet-plan generation is not part of this assistant" in rendered
    assert "Never include the sexual-activity field in response context" in rendered
