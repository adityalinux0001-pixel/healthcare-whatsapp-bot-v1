from __future__ import annotations

from app.llm.conversation_schemas import ConversationRoute


def normalize_conversation_route(route: ConversationRoute) -> ConversationRoute:
    # Saved hair-care routines are handled deterministically by the application.
    # Do not downgrade these intents to general QA: that would bypass saved-plan
    # retrieval and revision after the diet-to-hair product transition.

    # A profile recall without fields cannot be executed deterministically.
    if route.intent == "profile_recall" and not route.profile_fields:
        return route.model_copy(update={
            "intent": "clarification",
            "grounding_required": False,
        })

    return route
