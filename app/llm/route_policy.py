from __future__ import annotations

from app.llm.conversation_schemas import ConversationRoute


def normalize_conversation_route(route: ConversationRoute) -> ConversationRoute:
  
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
