from app.llm.conversation_schemas import ConversationRoute
from app.llm.prompts import CONVERSATION_ROUTER_PROMPT


def route_for(**overrides):
    data = {
        "intent": "general_health",
        "confidence": 0.98,
        "grounding_required": True,
    }
    data.update(overrides)
    return ConversationRoute.model_validate(data)


def test_normal_nutrition_question_is_a_health_route():
    route = route_for()
    assert route.intent == "general_health"
    assert route.grounding_required is True
    assert route.profile_updates == []


def test_saved_plan_route_has_explicit_plan_reference():
    route = route_for(
        intent="saved_plan_retrieval",
        grounding_required=False,
        plan_reference="day_number",
        plan_day_number=2,
        plan_scope="full",
    )
    assert route.plan_day_number == 2
    assert route.plan_scope == "full"


def test_profile_recall_route_contains_only_requested_fields():
    route = route_for(
        intent="profile_recall",
        grounding_required=False,
        profile_fields=["goal"],
    )
    assert route.profile_fields == ["goal"]
    assert route.profile_updates == []


def test_profile_update_requires_current_turn_evidence_in_contract():
    route = route_for(
        intent="profile_update",
        profile_updates=[
            {
                "field": "weight_kg",
                "value": "72",
                "evidence": "my weight is 72 kg",
                "confidence": 0.97,
            }
        ],
    )
    assert route.profile_updates[0].evidence
    assert route.profile_updates[0].confidence >= 0.90


def test_plan_modification_can_carry_a_clarification_without_db_side_effect():
    route = route_for(
        intent="plan_modification",
        grounding_required=False,
        plan_scope="none",
        clarification_question="Which foods are allowed during your fast?",
    )
    assert route.modification_instruction is None
    assert route.clarification_question


def test_router_prompt_distinguishes_generic_food_questions_from_saved_plan_retrieval():
    rendered = CONVERSATION_ROUTER_PROMPT.format(
        user_message="can I eat rice during weight gain?",
        history="USER: give me day 2 diet plan\nASSISTANT: I don't have a saved Day 2 plan yet.",
        summary="Goal is muscle gain.",
        profile={"goal": "muscle_gain", "weight_kg": 70},
    )
    assert "Do NOT turn a generic nutrition question into a saved-plan request" in rendered
    assert "previous Day 2 request" in rendered
