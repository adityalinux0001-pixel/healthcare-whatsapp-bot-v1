from __future__ import annotations


from typing import Any


# Relevant legacy safety context is included only when a value was actually saved.
# New hair onboarding does not pretend an unasked medical question was answered.
_HEALTH_SAFETY_FIELDS = ("allergies", "medical_conditions")

# Hair-related profile fields appropriate for regular grounded answers. Sensitive
# sexual-activity information is intentionally excluded from this list.
_HAIR_PERSONALIZATION_FIELDS = (
    "age", "city", "height_cm", "weight_kg", "hair_wash_frequency",
    "water_hardness", "sugary_food_drink_intake", "family_hair_loss",
    "family_hair_loss_relation", "dairy_intake",
)


def build_response_context(
    profile: dict[str, Any],
    history: list[dict],
    summary: str | None,
    route: Any | None,
) -> tuple[dict[str, Any], list[dict], str | None]:

    selected_fields: set[str] = set()
    selected_history_indices: list[int] = []
    use_summary = False

    if route is not None:
        selected_fields.update(getattr(route, "response_profile_fields", []) or [])
        selected_history_indices = list(getattr(route, "relevant_history_indices", []) or [])
        use_summary = bool(getattr(route, "use_long_term_memory", False))

        dialogue_act = getattr(route, "dialogue_act", "request")
 
        if dialogue_act not in {"follow_up", "accept_offer", "correction"}:
            selected_history_indices = []

   
        selected_history_indices = selected_history_indices[-2:]

        if bool(getattr(route, "grounding_required", False)):
   
            selected_fields.update(_HEALTH_SAFETY_FIELDS)
            selected_fields.update(_HAIR_PERSONALIZATION_FIELDS)

        selected_fields.discard("name")
        # Sensitive sexual-activity information is never part of ordinary QA
        # context, even if a model route mistakenly requests it. Explicit profile
        # recall is handled separately by the application.
        selected_fields.discard("sexually_active")

    # Keep only fields that actually exist in the persisted profile.
    response_profile = {
        field: profile[field]
        for field in selected_fields
        if field in profile
    }

    # Router indices are relative to the exact recent-history list it received.
    # De-duplicate while preserving chronological ordering and cap the context.
    valid_indices = {
        i for i in selected_history_indices
        if isinstance(i, int) and 0 <= i < len(history)
    }
    response_history = [history[i] for i in sorted(valid_indices)][:4]

    # If routing fails completely, keep a tiny fallback window rather than dumping
    # all history. This is deliberately conservative and cannot recreate the old
    # "compound the whole conversation" failure mode.
    if route is None:
        response_history = history[-2:] if history else []
        response_profile = {
            field: profile[field]
            for field in (*_HEALTH_SAFETY_FIELDS, *_HAIR_PERSONALIZATION_FIELDS)
            if field in profile
        }

    response_summary = (summary or None) if use_summary else None
    return response_profile, response_history, response_summary
