from __future__ import annotations

from app.llm.conversation_schemas import ConversationRoute


def normalize_conversation_route(route: ConversationRoute) -> ConversationRoute:
    """Apply application invariants after LLM semantic routing.

    The model proposes a semantic route; application policy decides whether that
    route is structurally safe to execute. This layer intentionally avoids
    question-specific keyword matching.
    """
    # A saved-plan handler must have a concrete reference. Otherwise there is no
    # deterministic DB lookup the application can safely execute.
    if route.intent == "saved_plan_retrieval" and route.plan_reference == "none":
        return route.model_copy(update={
            "intent": "general_health",
            "grounding_required": True,
            "plan_reference": "none",
            "plan_day_number": None,
            "plan_date": None,
            "plan_scope": "none",
            "plan_section": None,
        })

    # A profile recall without fields cannot be executed deterministically.
    if route.intent == "profile_recall" and not route.profile_fields:
        return route.model_copy(update={
            "intent": "clarification",
            "grounding_required": False,
        })

    return route
