"""Deterministic onboarding engine.

Design goal: onboarding must survive adversarial / garbage / out-of-order
input from real users (and QA testers deliberately trying to break it)
without ever looping forever, merging unrelated answers together, or
silently skipping a required field. To get that guarantee we do NOT let an
LLM decide what was said or what to ask next during onboarding — every
question is a fixed string and every answer is parsed by an explicit,
testable rule for that specific field only. There is nothing here whose
output can vary between two runs on the same input.

ONBOARDING_ORDER defines the single source of truth for field order. The
bot always targets `ONBOARDING_ORDER[i]`, the first field not yet answered
on the user's profile, and will not advance past it until that field
specifically has a valid value. A message is still allowed to fill in
*other* still-missing fields opportunistically (e.g. "male 39" supplies
gender + age even while the bot is still waiting on the target field) —
this keeps the flow natural — but only the current target field decides
whether the bot moves on.

Onboarding no longer collects the user's name — it starts directly at age.
"""

from __future__ import annotations

import re
from typing import Any

# ---------------------------------------------------------------------------
# Canonical field order + copy. Keep in sync with User.REQUIRED_FIELDS order
# in app/models.py (missing_fields() already returns this same order).
# ---------------------------------------------------------------------------

ONBOARDING_ORDER: list[str] = [
    "age", "gender", "height_cm", "weight_kg",
    "activity_level", "goal", "diet_preference",
    "allergies", "medical_conditions",
]

# Every question below ends with a "Reply like this:" example that shows the
# user the exact text they can send back — this is what makes each question
# self-service (no need to guess the expected format or wait for a retry hint).
QUESTIONS: dict[str, str] = {
    "age": (
        "Let's set up your profile — just a few quick questions 😊\n\n"
        "What is your age?\n\n"
        "*Reply like this:* 28"
    ),
    "gender": (
        "What is your gender?\n"
        "1️⃣ Male\n"
        "2️⃣ Female\n\n"
        "*Reply like this:* 1  (or type \"male\")"
    ),
    "height_cm": (
        "What is your height?\n\n"
        "*Reply like this:* 170  (cm)\n"
        "*Or like this:* 5 ft 7  (feet & inches)"
    ),
    "weight_kg": (
        "And your current weight?\n\n"
        "*Reply like this:* 65  (kg)\n"
        "*Or like this:* 143 lbs  (pounds)"
    ),
    "activity_level": (
        "How active is your day-to-day?\n"
        "1️⃣ Sedentary (little/no exercise)\n"
        "2️⃣ Light (1-2 days/week)\n"
        "3️⃣ Moderate (3-5 days/week)\n"
        "4️⃣ Active (6-7 days/week)\n\n"
        "*Reply like this:* 3  (or type \"moderate\")"
    ),
    "goal": (
        "What is your main goal?\n"
        "1️⃣ Lose weight\n"
        "2️⃣ Gain weight\n"
        "3️⃣ Maintain weight\n"
        "4️⃣ Gain muscle\n\n"
        "*Reply like this:* 1  (or type \"lose weight\")"
    ),
    "diet_preference": (
        "What is your diet preference?\n"
        "1️⃣ Vegetarian\n"
        "2️⃣ Non-vegetarian\n"
        "3️⃣ Eggetarian\n"
        "4️⃣ Vegan\n\n"
        "*Reply like this:* 2  (or type \"non-veg\")"
    ),
    "allergies": (
        "Do you have any food allergies?\n\n"
        "*Reply like this:* peanuts, dust\n"
        "*Or if you don't have any:* no"
    ),
    "medical_conditions": (
        "Do you have any medical conditions?\n\n"
        "*Reply like this:* diabetes, thyroid\n"
        "*Or if you don't have any:* no"
    ),
}

# Shown ONLY when the current target field's answer could not be understood.
# Kept short and always paired with the original question, same as a strict
# format-validator error, never a vague "I didn't understand you" loop — and
# always with a concrete example so the retry itself is self-service too.
RETRY_HINTS: dict[str, str] = {
    "age": "Hmm, I didn't catch that. Please send your age as just a number between 1 and 120.\n\n*Reply like this:* 28",
    "gender": "Please reply with \"male\" or \"female\" (or 1 / 2).\n\n*Reply like this:* male",
    "height_cm": "Please send your height in cm, or in feet/inches.\n\n*Reply like this:* 170  or  5 ft 7",
    "weight_kg": "Please send your weight in kg, or in lbs.\n\n*Reply like this:* 65  or  143 lbs",
    "activity_level": "Please reply with a number from 1-4, or a word like \"sedentary\" / \"active\".\n\n*Reply like this:* 3",
    "goal": "Please reply with a number from 1-4, or a word like \"lose weight\" / \"gain muscle\".\n\n*Reply like this:* 1",
    "diet_preference": "Please reply with a number from 1-4, or a word like \"veg\" / \"vegan\".\n\n*Reply like this:* 2",
    "allergies": "Please tell me your allergies, or reply \"no\" if you don't have any.\n\n*Reply like this:* peanuts, dust  or  no",
    "medical_conditions": "Please tell me your medical conditions, or reply \"no\" if you don't have any.\n\n*Reply like this:* diabetes  or  no",
}

_ACTIVITY_ALIASES = {
    "sedentary": "sedentary", "sedentry": "sedentary", "1": "sedentary",
    "inactive": "sedentary", "not active": "sedentary", "no exercise": "sedentary",
    "no workout": "sedentary", "sedentary lifestyle": "sedentary",
    "light": "light", "2": "light", "lightly active": "light",
    "moderate": "moderate", "3": "moderate", "moderately active": "moderate",
    "active": "active", "4": "active", "very active": "active",
}
_GOAL_ALIASES = {
    "lose weight": "weight_loss", "1": "weight_loss", "lose fat": "weight_loss",
    "fat loss": "weight_loss", "weight loss": "weight_loss", "weight_loss": "weight_loss",
    "gain weight": "weight_gain", "2": "weight_gain", "weight gain": "weight_gain",
    "weight_gain": "weight_gain", "put on weight": "weight_gain",
    "maintain": "maintain", "3": "maintain", "maintain weight": "maintain", "maintenance": "maintain",
    "gain muscle": "muscle_gain", "4": "muscle_gain", "gain muscles": "muscle_gain",
    "build muscle": "muscle_gain", "build muscles": "muscle_gain", "muscle gain": "muscle_gain",
    "put on muscle": "muscle_gain", "muscle_gain": "muscle_gain",
}
_DIET_ALIASES = {
    "veg": "veg", "1": "veg", "vegetarian": "veg", "vegeterian": "veg", "vegitarian": "veg",
    "non veg": "non_veg", "2": "non_veg", "non-veg": "non_veg", "nonveg": "non_veg",
    "non vegetarian": "non_veg", "non_vegetarian": "non_veg",
    "eggetarian": "eggetarian", "3": "eggetarian", "eggitarian": "eggetarian",
    "vegan": "vegan", "4": "vegan",
}
_GENDER_ALIASES = {
    "male": "male", "m": "male", "man": "male", "boy": "male",
    "female": "female", "f": "female", "woman": "female", "girl": "female",
}


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _fold(text: str) -> str:
    return text.casefold()


def looks_like_gibberish(token: str) -> bool:
    """True for keyboard-mash strings like 'kdkjhfrekj' (5+ consonants in a row,
    no vowel) — used to keep obvious junk out of the free-text health fields
    (allergies/medical_conditions)."""
    return bool(re.search(r"(?i)[bcdfghjklmnpqrstvwxyz]{5,}", token))


_HEALTH_NEGATIVE_WORDS = {
    "no", "not", "none", "nothing", "na", "nope", "nah", "never", "zero",
    "dont", "don't", "haven't", "havent",
}
_HEALTH_FILLER_WORDS = {
    "i", "have", "any", "allergies", "allergy", "allergen", "allergens",
    "medical", "condition", "conditions", "issue", "issues", "health",
    "problem", "problems", "disease", "diseases", "and", "or", "that",
    "know", "of", "to", "report", "there", "is", "are", "at", "all", "ve",
}


def is_clear_negative_health_reply(text: str) -> bool:
    """Catches every phrasing seen in the wild for 'I have none of that':
    'no', 'None', 'no i dont have', 'I dont have any allergies and
    medical conditions', etc. — by stripping filler words rather than
    matching a fixed list of exact sentences (which is what silently
    broke the earlier fullmatch-based version and caused repeat-looping).
    """
    tokens = re.findall(r"[a-z']+", _fold(text))
    if not tokens:
        return False
    meaningful = [t for t in tokens if t not in _HEALTH_FILLER_WORDS]
    if not meaningful:
        return False
    return all(t in _HEALTH_NEGATIVE_WORDS for t in meaningful)


def _bare(text: str) -> str:
    return _fold(text).strip(" .!?")


# ---------------------------------------------------------------------------
# Per-field parsers. Each takes the raw user text (plus light context) and
# returns a value or None. None means "did not answer this field" — the
# caller decides what to do about that (re-ask vs. leave for later).
# ---------------------------------------------------------------------------

def _parse_age(raw: str, is_target: bool) -> int | None:
    normalized = _fold(raw)
    m = re.search(r"\bage\s*(?:is|:|-)?\s*(\d{1,3})\b", normalized)
    if not m:
        m = re.search(r"\b(?:male|female|man|woman)\s*,?\s*(\d{1,3})\s*(?:years?|yrs?|yo)?\b", normalized)
    if not m:
        m = re.search(r"\b(\d{1,3})\s*(?:years?|yrs?|yo)\b", normalized)
    if not m and is_target:
        m = re.fullmatch(r"(\d{1,3})", _bare(raw))
    if m:
        age = int(m.group(1))
        if 1 <= age <= 120:
            return age
    return None


def _parse_gender(raw: str, is_target: bool) -> str | None:
    normalized = _fold(raw)
    m = re.search(r"\b(male|female|man|woman|boy|girl|m|f)\b", normalized)
    if m:
        return _GENDER_ALIASES.get(m.group(1))
    # Bare numeric menu replies ("1"/"2") are only accepted while gender is
    # the field actually being asked — same collision-safety rule used for
    # activity_level/goal/diet_preference, which also reuse these digits for
    # their own menus.
    if is_target:
        bare = _bare(raw)
        if bare == "1":
            return "male"
        if bare == "2":
            return "female"
    return None


def _parse_height_cm(raw: str, is_target: bool) -> float | None:
    normalized = _fold(raw)
    m = re.search(r"\b(\d{2,3}(?:\.\d+)?)\s*(?:cms?|centimeters?|centimetres?)\b", normalized)
    if m:
        val = float(m.group(1))
        return round(val, 2) if 100 <= val <= 250 else None
    m = re.search(r"\b(\d)\s*(?:'|ft|feet)\s*(\d{1,2})?\s*(?:\"|in|inches)?\b", normalized)
    if m:
        feet = int(m.group(1))
        inches = int(m.group(2)) if m.group(2) else 0
        cm = round((feet * 12 + inches) * 2.54, 2)
        return cm if 100 <= cm <= 250 else None
    m = re.search(r"\b(1\.\d{1,2})\s*m\b", normalized)
    if m:
        cm = round(float(m.group(1)) * 100, 2)
        return cm if 100 <= cm <= 250 else None
    if is_target:
        m = re.fullmatch(r"(\d{2,3}(?:\.\d+)?)", _bare(raw))
        if m:
            val = float(m.group(1))
            return round(val, 2) if 100 <= val <= 250 else None
    return None


def _parse_weight_kg(raw: str, is_target: bool) -> float | None:
    normalized = _fold(raw)
    m = re.search(r"\b(\d{2,3}(?:\.\d+)?)\s*(?:kgs?|kilograms?)\b", normalized)
    if m:
        val = float(m.group(1))
        return round(val, 2) if 20 <= val <= 350 else None
    m = re.search(r"\b(\d{2,3}(?:\.\d+)?)\s*(?:lbs?|pounds?)\b", normalized)
    if m:
        val = round(float(m.group(1)) * 0.453592, 2)
        return val if 20 <= val <= 350 else None
    if is_target:
        m = re.fullmatch(r"(\d{2,3}(?:\.\d+)?)", _bare(raw))
        if m:
            val = float(m.group(1))
            return round(val, 2) if 20 <= val <= 350 else None
    return None


def _parse_enum(raw: str, aliases: dict[str, str], is_target: bool) -> str | None:
    """Short codes ("1"-"4", single letters) are ONLY ever accepted when this
    field is the current target. activity_level/goal/diet_preference each
    reuse "1".."4" for their own menu, so without this gate a bare "3" typed
    while some other field is being asked would get misfiled here purely by
    coincidence (this was a real bug caught in testing)."""
    bare = _bare(raw)
    if bare in aliases and (is_target or len(bare) > 2):
        return aliases[bare]
    normalized = _fold(raw)
    # Longest alias phrases first so "very active" wins over "active".
    for phrase in sorted(aliases, key=len, reverse=True):
        if len(phrase) <= 2 and not is_target:
            continue  # skip 1-2 char aliases ("1","m") unless this IS the target field
        if re.search(rf"\b{re.escape(phrase)}\b", normalized):
            return aliases[phrase]
    return None


def _parse_health_text(raw: str, field_limit: int) -> str | None:
    cleaned = _norm(raw)
    if not cleaned:
        return None
    if is_clear_negative_health_reply(cleaned):
        return "None reported"
    # Reject pure junk (e.g. "kdkjhfrekj", "??", ".") as a real answer for a
    # free-text safety field rather than silently storing it.
    letters_only = re.sub(r"[^a-zA-Z]", "", cleaned)
    if len(letters_only) < 2:
        return None
    if len(cleaned.split()) == 1 and looks_like_gibberish(cleaned):
        return None
    return cleaned[:field_limit]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_fields_from_text(
    text: str,
    current_target: str,
    missing_fields: list[str],
    history: list[dict] | None = None,
) -> dict[str, Any]:
    """Opportunistically pull every field this message answers.

    `current_target` gets the most permissive parsing (bare numbers/words
    are accepted for it specifically); every other still-missing field only
    matches on explicit, unambiguous phrasing so an answer meant for one
    question is never mistaken for another.
    """
    raw = _norm(text)
    result: dict[str, Any] = {}
    missing = set(missing_fields)

    if "age" in missing:
        value = _parse_age(raw, is_target=(current_target == "age"))
        if value is not None:
            result["age"] = value

    if "gender" in missing:
        value = _parse_gender(raw, is_target=(current_target == "gender"))
        if value:
            result["gender"] = value

    if "height_cm" in missing:
        value = _parse_height_cm(raw, is_target=(current_target == "height_cm"))
        if value is not None:
            result["height_cm"] = value

    if "weight_kg" in missing:
        value = _parse_weight_kg(raw, is_target=(current_target == "weight_kg"))
        if value is not None:
            result["weight_kg"] = value

    if "activity_level" in missing:
        value = _parse_enum(raw, _ACTIVITY_ALIASES, is_target=(current_target == "activity_level"))
        if value:
            result["activity_level"] = value

    if "goal" in missing:
        value = _parse_enum(raw, _GOAL_ALIASES, is_target=(current_target == "goal"))
        if value:
            result["goal"] = value

    if "diet_preference" in missing:
        value = _parse_enum(raw, _DIET_ALIASES, is_target=(current_target == "diet_preference"))
        if value:
            result["diet_preference"] = value

    # Free-text safety fields: only ever parsed when they are the CURRENT
    # target. Unlike the structured fields above, arbitrary prose here could
    # otherwise be misfiled against the wrong question (e.g. a stray
    # "no allergies" mentioned while answering the goal question).
    if current_target == "allergies" and "allergies" in missing:
        value = _parse_health_text(raw, 1000)
        if value:
            result["allergies"] = value

    if current_target == "medical_conditions" and "medical_conditions" in missing:
        value = _parse_health_text(raw, 1500)
        if value:
            result["medical_conditions"] = value

    return result