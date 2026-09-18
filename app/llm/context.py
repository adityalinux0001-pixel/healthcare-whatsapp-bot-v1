from __future__ import annotations

"""Context assembly for conversational responses.

The router decides *which* saved state and prior turns are relevant. This module
applies those decisions and constructs a deliberately small response context.
It keeps the full user profile and the raw message history out of the answer
prompt unless they are actually needed for the current turn.
"""

from typing import Any


# These fields are useful for grounded health advice as hidden safety context.
# They are not a mandate to mention or repeat them in the response.
_HEALTH_SAFETY_FIELDS = ("allergies", "medical_conditions")


def build_response_context(
    profile: dict[str, Any],
    history: list[dict],
    summary: str | None,
    route: Any | None,
) -> tuple[dict[str, Any], list[dict], str | None]:
    """Return minimum necessary context for the final answer generator.

    The semantic router is trusted only for *relevance selection*. The application
    still enforces hard boundaries: name is never exposed to ordinary answering,
    at most four prior turns are selected, indices must be valid, and long-term
    memory is opt-in per turn.
    """
    selected_fields: set[str] = set()
    selected_history_indices: list[int] = []
    use_summary = False

    if route is not None:
        selected_fields.update(getattr(route, "response_profile_fields", []) or [])
        selected_history_indices = list(getattr(route, "relevant_history_indices", []) or [])
        use_summary = bool(getattr(route, "use_long_term_memory", False))

        # Grounded health answers get the minimum safety-critical profile context
        # regardless of whether the router explicitly listed it.
        if bool(getattr(route, "grounding_required", False)):
            selected_fields.update(_HEALTH_SAFETY_FIELDS)

        # Never leak name into normal answer context. Name recall is handled by the
        # deterministic profile-recall path instead.
        selected_fields.discard("name")

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
            for field in (*_HEALTH_SAFETY_FIELDS, "diet_preference", "goal")
            if field in profile
        }

    response_summary = (summary or None) if use_summary else None
    return response_profile, response_history, response_summary
