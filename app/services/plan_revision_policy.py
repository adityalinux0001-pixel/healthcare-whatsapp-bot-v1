from __future__ import annotations


import difflib
from datetime import datetime, timedelta

DUPLICATE_SIMILARITY_THRESHOLD = 0.55
DUPLICATE_WINDOW = timedelta(minutes=30)


def is_duplicate_modification(
    previous_instruction: str | None,
    previous_modified_at: datetime | None,
    new_instruction: str,
    now: datetime,
) -> bool:
   
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
