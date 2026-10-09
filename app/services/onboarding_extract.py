"""Deterministic, LLM-free onboarding for the hair-loss assistant.

Only explicit answers are stored. Numbered menu answers are accepted only for the
question currently being asked, preventing the same number from filling a wrong field.
"""
from __future__ import annotations

import re
from typing import Any

ONBOARDING_ORDER: list[str] = [
    "age", "city", "hair_wash_frequency", "water_hardness", "height_cm",
    "weight_kg", "sugary_food_drink_intake", "sexually_active",
    "family_hair_loss", "family_hair_loss_relation", "dairy_intake",
]

QUESTIONS: dict[str, str] = {
    # The main questions/options below preserve the manager-provided wording.
    # Short follow-up prompts are used only when a user answers one part of a
    # combined question and leaves the other field empty.
    "age": (
        "**How old are you, and where do you currently live?**\n\n"
        "- Age: ___\n\n- City: ___"
    ),
    "city": (
        "Where do you currently live?\n\n"
        "- City: ___"
    ),
    "hair_wash_frequency": (
        "**How often do you wash your hair?**\n\n"
        "- Daily\n- 2–3 times a week\n- Once a week\n- Less than once a week"
    ),
    "water_hardness": (
        "**What type of water do you usually use to wash your hair?**\n\n"
        "- Soft water\n- Moderately hard water\n- Hard water\n- Very hard water\n- I'm not sure"
    ),
    "height_cm": (
        "**What are your current height and weight?**\n\n"
        "- Height: ___ cm / ft & inches\n- Weight: ___ kg / lbs"
    ),
    "weight_kg": (
        "What is your current weight?\n\n"
        "- Weight: ___ kg / lbs"
    ),
    "sugary_food_drink_intake": (
        "**How much sugary food or drinks do you normally consume?**\n\n"
        "- None or very little\n- Low\n- Moderate\n- High\n- Very high\n\n"
        "*Examples: sweets, desserts, sugary tea/coffee, soft drinks, packaged juices, etc.*"
    ),
    "sexually_active": (
        "**Are you currently sexually active?**\n\n"
        "- Yes\n- No\n- Prefer not to say"
    ),
    "family_hair_loss": (
        "**Does hair loss run in your family history ?**\n\n"
        "- Yes\n- No\n- Not sure"
    ),
    "family_hair_loss_relation": (
        "**Who in your family has experienced noticeable hair loss?**\n\n"
        "- Father\n- Mother\n- Brother/Sister\n- Grandparent\n"
        "- Multiple family members\n- Other"
    ),
    "dairy_intake": (
        "**How much dairy do you normally consume?**\n\n"
        "- None\n- Low — occasionally\n- Moderate — once a day\n"
        "- High — 2–3 times a day\n- Very high — more than 3 times a day\n\n"
        "*Examples: milk, curd/yogurt, paneer, cheese, butter, cream, etc.*"
    ),
}

RETRY_HINTS: dict[str, str] = {
    "age": "Please share your age and city. *Reply like this:* 28, Indore",
    "city": "Please share your city. *Reply like this:* Indore",
    "hair_wash_frequency": "Please choose 1–4 for how often you wash your hair. *Reply like this:* 2",
    "water_hardness": "Please choose 1–5, or say 'I'm not sure'. *Reply like this:* 5",
    "height_cm": "Please share height and weight with units. *Reply like this:* 170 cm, 65 kg",
    "weight_kg": "Please share your weight in kg or lbs. *Reply like this:* 65 kg",
    "sugary_food_drink_intake": "Please choose 1–5. *Reply like this:* 3 (moderate)",
    "sexually_active": "Please choose yes, no, or 'prefer not to say'. *Reply like this:* prefer not to say",
    "family_hair_loss": "Please reply yes, no, or not sure. *Reply like this:* not sure",
    "family_hair_loss_relation": "Please select who in your family. *Reply like this:* father",
    "dairy_intake": "Please choose 1–5. *Reply like this:* 3 (once a day)",
}

_WASH = {
    "daily": "daily", "every day": "daily", "everyday": "daily", "1": "daily",
    "2-3 times a week": "2_3_times_week", "2 to 3 times a week": "2_3_times_week",
    "2–3 times a week": "2_3_times_week", "twice a week": "2_3_times_week",
    "2-3 times per week": "2_3_times_week", "2": "2_3_times_week",
    "once a week": "once_week", "one time a week": "once_week", "weekly": "once_week", "3": "once_week",
    "less than once a week": "less_than_once_week", "less than once weekly": "less_than_once_week",
    "less than weekly": "less_than_once_week", "4": "less_than_once_week",
}
_WATER = {
    "soft": "soft", "soft water": "soft", "1": "soft",
    "moderately hard": "moderately_hard", "moderately hard water": "moderately_hard", "2": "moderately_hard",
    "hard": "hard", "hard water": "hard", "3": "hard",
    "very hard": "very_hard", "very hard water": "very_hard", "4": "very_hard",
    "not sure": "not_sure", "not sure about water": "not_sure", "unsure": "not_sure", "i don't know": "not_sure", "i dont know": "not_sure", "5": "not_sure",
}
_SUGAR = {
    "none": "none_or_very_little", "none or very little": "none_or_very_little",
    "very little": "none_or_very_little", "very low": "none_or_very_little", "1": "none_or_very_little",
    "low": "low", "2": "low", "moderate": "moderate", "medium": "moderate", "3": "moderate",
    "high": "high", "4": "high", "very high": "very_high", "5": "very_high",
}
_SEXUALLY_ACTIVE = {
    "yes": "yes", "y": "yes", "1": "yes", "no": "no", "n": "no", "2": "no",
    "prefer not to say": "prefer_not_to_say", "prefer not": "prefer_not_to_say",
    "rather not say": "prefer_not_to_say", "3": "prefer_not_to_say",
}
_FAMILY_HISTORY = {
    "yes": "yes", "y": "yes", "1": "yes", "no": "no", "n": "no", "2": "no",
    "not sure": "not_sure", "unsure": "not_sure", "maybe": "not_sure", "3": "not_sure",
}
_RELATION = {
    "father": "father", "dad": "father", "1": "father",
    "mother": "mother", "mom": "mother", "mum": "mother", "2": "mother",
    "brother": "sibling", "sister": "sibling", "sibling": "sibling", "brother or sister": "sibling", "brother/sister": "sibling", "3": "sibling",
    "grandparent": "grandparent", "grandparents": "grandparent", "grandmother": "grandparent", "grandfather": "grandparent", "4": "grandparent",
    "multiple family members": "multiple", "many family members": "multiple", "both parents": "multiple", "5": "multiple",
    "other": "other", "someone else": "other", "6": "other",
}
_DAIRY = {
    "none": "none", "no dairy": "none", "0": "none", "1": "none",
    "low": "low", "occasionally": "low", "sometimes": "low", "2": "low",
    "moderate": "moderate", "once a day": "moderate", "daily": "moderate", "3": "moderate",
    "high": "high", "2-3 times a day": "high", "2 to 3 times a day": "high", "4": "high",
    "very high": "very_high", "more than 3 times a day": "very_high", "over 3 times a day": "very_high", "5": "very_high",
}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _fold(value: str) -> str:
    return _norm(value).casefold().replace("’", "'")


def _bare(value: str) -> str:
    return _fold(value).strip(" .!?\t\n")


def _parse_age(raw: str, is_target: bool) -> int | None:
    normalized = _fold(raw)
    match = re.search(r"\bage\s*(?:is|:|-)?\s*(\d{1,3})\b", normalized)
    if not match:
        match = re.search(r"\b(\d{1,3})\s*(?:years? old|years?|yrs?|yo)\b", normalized)
    if not match and is_target:
        # Supports a combined first answer such as "28, Indore" without needing "years".
        match = re.match(r"^\s*(\d{1,3})\b", normalized)
    if match:
        age = int(match.group(1))
        if 1 <= age <= 120:
            return age
    return None


def _parse_city(raw: str, is_target: bool) -> str | None:
    normalized = _norm(raw)
    labeled = re.search(r"\bcity\s*[:=-]?\s*([A-Za-z][A-Za-z .'-]{1,90})", normalized, flags=re.I)
    if labeled:
        city = labeled.group(1).strip(" .,-")
        return city[:100] if city else None
    if is_target:
        city = re.sub(r"^(my city is|i live in|currently in|city is)\s+", "", normalized, flags=re.I).strip(" .,-")
        if re.search(r"[A-Za-z]", city) and len(city) <= 100 and len(city.split()) <= 8:
            return city
    # Opportunistically extract city from the age+city combined answer.
    match = re.match(r"^\s*\d{1,3}(?:\s*(?:years? old|years?|yrs?|yo))?\s*[,;|/-]\s*([A-Za-z][A-Za-z .'-]{1,90})\s*$", normalized, flags=re.I)
    if match:
        return match.group(1).strip(" .,-")[:100]
    return None


def _parse_height_cm(raw: str, is_target: bool) -> float | None:
    normalized = _fold(raw)
    match = re.search(r"\b(\d{2,3}(?:\.\d+)?)\s*(?:cms?|centimeters?|centimetres?)\b", normalized)
    if match:
        value = float(match.group(1))
        return round(value, 2) if 100 <= value <= 250 else None
    match = re.search(r"\b(\d)\s*(?:'|ft|feet)\s*(\d{1,2})?\s*(?:\"|in|inches)?\b", normalized)
    if match:
        feet, inches = int(match.group(1)), int(match.group(2) or 0)
        cm = round((feet * 12 + inches) * 2.54, 2)
        return cm if 100 <= cm <= 250 else None
    match = re.search(r"\b(1\.\d{1,2})\s*m\b", normalized)
    if match:
        cm = round(float(match.group(1)) * 100, 2)
        return cm if 100 <= cm <= 250 else None
    if is_target:
        match = re.fullmatch(r"(\d{2,3}(?:\.\d+)?)", _bare(raw))
        if match:
            value = float(match.group(1))
            return round(value, 2) if 100 <= value <= 250 else None
    return None


def _parse_weight_kg(raw: str, is_target: bool) -> float | None:
    normalized = _fold(raw)
    match = re.search(r"\b(\d{2,3}(?:\.\d+)?)\s*(?:kgs?|kilograms?)\b", normalized)
    if match:
        value = float(match.group(1))
        return round(value, 2) if 20 <= value <= 350 else None
    match = re.search(r"\b(\d{2,3}(?:\.\d+)?)\s*(?:lbs?|pounds?)\b", normalized)
    if match:
        value = round(float(match.group(1)) * 0.453592, 2)
        return value if 20 <= value <= 350 else None
    if is_target:
        match = re.fullmatch(r"(\d{2,3}(?:\.\d+)?)", _bare(raw))
        if match:
            value = float(match.group(1))
            return round(value, 2) if 20 <= value <= 350 else None
    return None


def _parse_enum(raw: str, aliases: dict[str, str], is_target: bool) -> str | None:
    bare = _bare(raw)
    if bare in aliases and (is_target or not bare.isdigit()):
        return aliases[bare]
    normalized = _fold(raw)
    # Explicit worded answers may be captured out of order; numeric answers may not.
    for phrase in sorted((k for k in aliases if not k.isdigit()), key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized):
            return aliases[phrase]
    return None


def _parse_family_history(raw: str, is_target: bool) -> str | None:
    normalized = _fold(raw)
    if any(phrase in normalized for phrase in ("not sure", "unsure", "don't know", "dont know", "not certain")):
        return "not_sure"
    if re.search(r"\b(no|none|nobody|not in my family|no family history)\b", normalized):
        return "no"
    if re.search(r"\b(yes|yeah|yep|father|dad|mother|mom|mum|brother|sister|grandparent|family members)\b", normalized):
        return "yes"
    if is_target and _bare(raw) in {"1", "2", "3"}:
        return {"1": "yes", "2": "no", "3": "not_sure"}[_bare(raw)]
    return None


def _parse_relation(raw: str, is_target: bool) -> str | None:
    normalized = _fold(raw)
    for phrase in sorted((key for key in _RELATION if not key.isdigit()), key=len, reverse=True):
        if re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized):
            return _RELATION[phrase]
    if is_target and _bare(raw) in {"1", "2", "3", "4", "5", "6"}:
        return _RELATION[_bare(raw)]
    return None


def extract_fields_from_text(
    text: str,
    current_target: str,
    missing_fields: list[str],
    history: list[dict] | None = None,
) -> dict[str, Any]:
    raw = _norm(text)
    normalized = _fold(raw)
    result: dict[str, Any] = {}
    missing = set(missing_fields)

    if "age" in missing:
        age = _parse_age(raw, current_target == "age")
        if age is not None:
            result["age"] = age
    if "city" in missing:
        city = _parse_city(raw, current_target == "city")
        if city:
            result["city"] = city

    if "hair_wash_frequency" in missing:
        value = _parse_enum(raw, _WASH, current_target == "hair_wash_frequency")
        if value:
            result["hair_wash_frequency"] = value
    if "water_hardness" in missing:
        value = _parse_enum(raw, _WATER, current_target == "water_hardness")
        if value:
            result["water_hardness"] = value

    if "height_cm" in missing:
        value = _parse_height_cm(raw, current_target == "height_cm")
        if value is not None:
            result["height_cm"] = value
    if "weight_kg" in missing:
        value = _parse_weight_kg(raw, current_target == "weight_kg")
        if value is not None:
            result["weight_kg"] = value

    if "sugary_food_drink_intake" in missing:
        value = _parse_enum(raw, _SUGAR, current_target == "sugary_food_drink_intake")
        if value:
            result["sugary_food_drink_intake"] = value
    if "sexually_active" in missing and current_target == "sexually_active":
        value = _parse_enum(raw, _SEXUALLY_ACTIVE, True)
        if value:
            result["sexually_active"] = value
    if "family_hair_loss" in missing:
        value = _parse_family_history(raw, current_target == "family_hair_loss")
        if value:
            result["family_hair_loss"] = value
            if value == "yes":
                relation = _parse_relation(raw, current_target == "family_hair_loss_relation")
                if relation:
                    result["family_hair_loss_relation"] = relation
    if "family_hair_loss_relation" in missing and current_target == "family_hair_loss_relation":
        value = _parse_relation(raw, True)
        if value:
            result["family_hair_loss_relation"] = value
    if "dairy_intake" in missing:
        value = _parse_enum(raw, _DAIRY, current_target == "dairy_intake")
        if value:
            result["dairy_intake"] = value

    # Explicit corrections may update hair profile fields after onboarding too.
    if current_target == "city" and not result.get("city") and normalized in {"prefer not to say", "skip"}:
        result["city"] = "Not provided"
    return result
