from __future__ import annotations

"""Duplicate-request detection for same-day plan modifications.

revise_today_plan() regenerates the plan via a fresh, non-deterministic LLM
call every time it runs. If the user restates essentially the same request
shortly after it was already applied -- often because a prior reply confused
them about whether anything happened -- regenerating again produces a
different-but-equivalent plan. Across a couple of turns this reads as the
bot "forgetting" its own update or drifting the plan for no real reason.

This module is a pure decision function so it can be unit tested without any
database or LLM dependency, matching plan_validation.py's style.
"""

import difflib
from datetime import datetime, timedelta

# Heuristic thresholds. Not exact-match dedup -- close paraphrases of the same
# complaint ("I missed my workout" / "i just only said i missed today's
# exercise!") should still count as the same request.
DUPLICATE_SIMILARITY_THRESHOLD = 0.55
DUPLICATE_WINDOW = timedelta(minutes=30)


def is_duplicate_modification(
    previous_instruction: str | None,
    previous_modified_at: datetime | None,
    new_instruction: str,
    now: datetime,
) -> bool:
    """True when `new_instruction` is close enough to the last-applied
    instruction, and recent enough, that reusing the existing plan is more
    correct than regenerating a new one.
    """
    if not previous_instruction or not previous_modified_at:
        return False
    if not new_instruction or not new_instruction.strip():
        return False
    if now - previous_modified_at > DUPLICATE_WINDOW:
        return False

    similarity = difflib.SequenceMatcher(
        None,
        previous_instruction.strip().lower(),
        new_instruction.strip().lower(),
    ).ratio()
    return similarity >= DUPLICATE_SIMILARITY_THRESHOLD
