from __future__ import annotations

import re
from typing import Any


ONBOARDING_ORDER: list[str] = [
    "age", "city",
    "hair_wash_frequency", "water_hardness",
    "height_cm", "weight_kg",
    "sugary_food_drink_frequency",
    "sexually_active",
    "family_hair_loss", "family_hair_loss_relation",
    "dairy_intake",
]


QUESTIONS: dict[str, str] = {
    "age": (
        "Let's get to know you with a few quick questions 😊\n\n"
        "How old are you, and which city do you live in?\n\n"
        "*Age:* 28\n"
        "*City:* Indore"
    ),
    "city": (
        "Which city do you currently live in?\n\n"
        "*Reply like this:* Indore"
    ),
    "hair_wash_frequency": (
        "How often do you wash your hair?\n\n"
        "1️⃣ Daily\n"
        "2️⃣ 2–3 times a week\n"
        "3️⃣ Once a week\n"
        "4️⃣ Less than once a week\n\n"
        "*Reply with 1, 2, 3 or 4.*"
    ),
    "water_hardness": (
        "What type of water do you usually use to wash your hair?\n\n"
        "1️⃣ Soft water\n"
        "2️⃣ Moderately hard water\n"
        "3️⃣ Hard water\n"
        "4️⃣ Very hard water\n"
        "5️⃣ I'm not sure\n\n"
        "*Reply with 1, 2, 3, 4 or 5.*"
    ),
    "height_cm": (
        "What is your current height and weight?\n\n"
        "*Height:* 170 cm\n"
        "*Weight:* 65 kg"
    ),
    "weight_kg": (
        "What is your current weight?\n\n"
        "*Reply like this:* 65 kg  (or 143 lbs)"
    ),
    "sugary_food_drink_frequency": (
        "How much sugary food or drinks do you usually have?\n\n"
        "1️⃣ None or very little\n"
        "2️⃣ Low\n"
        "3️⃣ Moderate\n"
        "4️⃣ High\n"
        "5️⃣ Very high\n\n"
        "*Reply with 1, 2, 3, 4 or 5.*\n"
        "Examples: sweets, desserts, sugary tea/coffee, soft drinks, packaged juices."
    ),
    "sexually_active": (
        "Are you currently sexually active?\n\n"
        "1️⃣ Yes\n"
        "2️⃣ No\n"
        "3️⃣ Prefer not to say\n\n"
        "*Reply with 1, 2 or 3.*"
    ),
    "family_hair_loss": (
        "Does hair loss run in your family?\n\n"
        "1️⃣ Yes\n"
        "2️⃣ No\n"
        "3️⃣ Not sure\n\n"
        "*Reply with 1, 2 or 3.*"
    ),
    "family_hair_loss_relation": (
        "Who in your family has had noticeable hair loss?\n\n"
        "1️⃣ Father\n"
        "2️⃣ Mother\n"
        "3️⃣ Brother/Sister\n"
        "4️⃣ Grandparent\n"
        "5️⃣ Multiple family members\n"
        "6️⃣ Other\n\n"
        "*Reply with a number (you can also type the relation).*"
    ),
    "dairy_intake": (
        "How much dairy do you usually have?\n\n"
        "1️⃣ None\n"
        "2️⃣ Low — occasionally\n"
        "3️⃣ Moderate — once a day\n"
        "4️⃣ High — 2–3 times a day\n"
        "5️⃣ Very high — more than 3 times a day\n\n"
        "*Reply with 1, 2, 3, 4 or 5.*\n"
        "Examples: milk, curd/yogurt, paneer, cheese, butter, cream."
    ),
}


RETRY_HINTS: dict[str, str] = {
    "age": "Please send your age as a number. If you can, include your city too.\n\n*Example:* 28, Indore",
    "city": "Please send the city you currently live in.\n\n*Example:* Indore",
    "hair_wash_frequency": "Please reply with 1, 2, 3 or 4.\n\n*Example:* 2",
    "water_hardness": "Please reply with 1, 2, 3, 4 or 5.\n\n*Example:* 3",
    "height_cm": "Please send your height. You can use cm or feet/inches.\n\n*Example:* 170 cm or 5 ft 7 in",
    "weight_kg": "Please send your weight in kg or lbs.\n\n*Example:* 65 kg or 143 lbs",
    "sugary_food_drink_frequency": "Please reply with 1, 2, 3, 4 or 5.\n\n*Example:* 3",
    "sexually_active": "Please reply with 1, 2 or 3.\n\n*Example:* 3",
    "family_hair_loss": "Please reply with 1, 2 or 3.\n\n*Example:* 1",
    "family_hair_loss_relation": "Please reply with a number or type the relation.\n\n*Example:* 1 (Father)",
    "dairy_intake": "Please reply with 1, 2, 3, 4 or 5.\n\n*Example:* 2",
}


_ENUM_ALIASES = {
    "hair_wash_frequency": {
        "1": "daily", "daily": "daily", "every day": "daily",
        "2": "2_3_times_week", "2-3 times a week": "2_3_times_week",
        "2–3 times a week": "2_3_times_week", "twice a week": "2_3_times_week",
        "three times a week": "2_3_times_week", "2 times a week": "2_3_times_week",
        "3": "once_week", "once a week": "once_week", "weekly": "once_week",
        "4": "less_once_week", "less than once a week": "less_once_week",
    },
    "water_hardness": {
        "1": "soft", "soft": "soft", "soft water": "soft",
        "2": "moderately_hard", "moderately hard": "moderately_hard", "moderately hard water": "moderately_hard",
        "3": "hard", "hard water": "hard",
        "4": "very_hard", "very hard": "very_hard", "very hard water": "very_hard",
        "5": "not_sure", "not sure": "not_sure", "unsure": "not_sure", "i don't know": "not_sure", "dont know": "not_sure",
    },
    "sugary_food_drink_frequency": {
        "1": "none_or_very_little", "none": "none_or_very_little", "very little": "none_or_very_little", "none or very little": "none_or_very_little",
        "2": "low", "low": "low",
        "3": "moderate", "moderate": "moderate",
        "4": "high", "high": "high",
        "5": "very_high", "very high": "very_high",
    },
    "sexually_active": {
        "1": "yes", "yes": "yes",
        "2": "no", "no": "no",
        "3": "prefer_not_to_say", "prefer not to say": "prefer_not_to_say", "prefer not": "prefer_not_to_say",
    },
    "family_hair_loss": {
        "1": "yes", "yes": "yes",
        "2": "no", "no": "no",
        "3": "not_sure", "not sure": "not_sure", "unsure": "not_sure",
    },
    "family_hair_loss_relation": {
        "1": "father", "father": "father",
        "2": "mother", "mother": "mother",
        "3": "sibling", "brother": "sibling", "sister": "sibling", "brother/sister": "sibling", "brother sister": "sibling",
        "4": "grandparent", "grandparent": "grandparent", "grandfather": "grandparent", "grandmother": "grandparent",
        "5": "multiple_family_members", "multiple family members": "multiple_family_members", "many family members": "multiple_family_members",
        "6": "other", "other": "other",
    },
    "dairy_intake": {
        "1": "none", "none": "none", "no dairy": "none",
        "2": "low", "low": "low", "occasionally": "low",
        "3": "moderate", "moderate": "moderate", "once a day": "moderate",
        "4": "high", "high": "high", "2-3 times a day": "high", "2–3 times a day": "high",
        "5": "very_high", "very high": "very_high", "more than 3 times a day": "very_high",
    },
}

_GIBBERISH_RE = re.compile(r"(?i)[bcdfghjklmnpqrstvwxyz]{6,}")


def _norm(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _fold(text: str) -> str:
    return text.casefold()


def _bare(text: str) -> str:
    return _fold(text).strip(" .!?\n")


def _looks_like_gibberish(text: str) -> bool:
    return bool(_GIBBERISH_RE.search(text))


def _parse_age(raw: str, is_target: bool) -> int | None:
    normalized = _fold(raw)
    patterns = [
        r"\bage\s*(?:is|:|-)?\s*(\d{1,3})\b",
        r"\b(?:male|female|man|woman)\s*,?\s*(\d{1,3})\s*(?:years?|yrs?|yo)?\b",
        r"\b(\d{1,3})\s*(?:years?|yrs?|yo)\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, normalized)
        if m:
            age = int(m.group(1))
            return age if 1 <= age <= 120 else None
    # Common combined reply to the first onboarding question: "28, Indore".
    m = re.match(r"^\s*(\d{1,3})\s*(?:,|;|-)\s*", normalized)
    if m:
        age = int(m.group(1))
        return age if 1 <= age <= 120 else None
    if is_target:
        m = re.fullmatch(r"(\d{1,3})", _bare(raw))
        if m:
            age = int(m.group(1))
            return age if 1 <= age <= 120 else None
    return None


def _parse_city(raw: str, is_target: bool) -> str | None:
    normalized = _norm(raw)
    lowered = normalized.casefold()
    patterns = [
        r"(?:city|live in|living in|based in|from)\s*(?:is|:|-)?\s*([A-Za-z][A-Za-z .\'-]{1,80})$",
        r"^\d{1,3}\s*(?:years?|yrs?)?\s*[,;-]\s*([A-Za-z][A-Za-z .\'-]{1,80})$",
        r"^\d{1,3}\s*[,/-]\s*([A-Za-z][A-Za-z .\'-]{1,80})$",
    ]
    for pattern in patterns:
        m = re.search(pattern, normalized, re.IGNORECASE)
        if m:
            value = m.group(1).strip(" .,-")
            return value[:100] if value else None
    if is_target:
        value = normalized.strip(" .,-")
        if not value or len(value) > 100 or re.search(r"\d", value) or _looks_like_gibberish(value):
            return None
        # Don't store a sentence as a city when the current question asks only for a city.
        if len(value.split()) > 8:
            return None
        return value
    return None


def _parse_height_cm(raw: str, is_target: bool) -> float | None:
    normalized = _fold(raw)
    m = re.search(r"\b(\d{2,3}(?:\.\d+)?)\s*(?:cms?|centimeters?|centimetres?)\b", normalized)
    if m:
        val = float(m.group(1))
        return round(val, 2) if 100 <= val <= 250 else None
    m = re.search(r"""\b(\d)\s*(?:['"]|ft|feet)\s*(\d{1,2})?\s*(?:"|in|inches)?\b""", normalized)
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


def _parse_choice(raw: str, field: str, is_target: bool) -> str | None:
    aliases = _ENUM_ALIASES[field]
    bare = _bare(raw)
    if bare in aliases and (is_target or not bare.isdigit()):
        return aliases[bare]
    normalized = _fold(raw)
    # Numeric menu choices only count when this field is the current question.
    if is_target:
        for key, value in aliases.items():
            if key.isdigit() and re.fullmatch(re.escape(key), bare):
                return value
    for phrase in sorted((k for k in aliases if not k.isdigit()), key=len, reverse=True):
        if re.search(rf"(?<![a-z]){re.escape(phrase)}(?![a-z])", normalized):
            return aliases[phrase]
    return None


def _parse_relation(raw: str, is_target: bool) -> str | None:
    value = _parse_choice(raw, "family_hair_loss_relation", is_target)
    if value:
        return value
    if not is_target:
        return None
    cleaned = _norm(raw)
    if not cleaned or len(cleaned) > 100 or _looks_like_gibberish(cleaned):
        return None
    return cleaned


def _parse_other_free_text(raw: str, limit: int = 100) -> str | None:
    cleaned = _norm(raw)
    if not cleaned or len(cleaned) > limit or _looks_like_gibberish(cleaned):
        return None
    return cleaned[:limit]


def extract_fields_from_text(
    text: str,
    current_target: str,
    missing_fields: list[str],
    history: list[dict] | None = None,
) -> dict[str, Any]:
    """Deterministic onboarding extraction. No LLM call is needed for these fields."""
    raw = _norm(text)
    result: dict[str, Any] = {}
    missing = set(missing_fields)

    if "age" in missing:
        value = _parse_age(raw, is_target=(current_target == "age"))
        if value is not None:
            result["age"] = value

    if "city" in missing:
        value = _parse_city(raw, is_target=(current_target == "city"))
        if value:
            result["city"] = value

    for field in (
        "hair_wash_frequency", "water_hardness", "sugary_food_drink_frequency",
        "sexually_active", "family_hair_loss", "dairy_intake",
    ):
        if field in missing:
            value = _parse_choice(raw, field, is_target=(current_target == field))
            if value:
                result[field] = value

    if "family_hair_loss_relation" in missing:
        value = _parse_relation(raw, is_target=(current_target == "family_hair_loss_relation"))
        if value:
            result["family_hair_loss_relation"] = value

    if "height_cm" in missing:
        value = _parse_height_cm(raw, is_target=(current_target == "height_cm"))
        if value is not None:
            result["height_cm"] = value

    if "weight_kg" in missing:
        value = _parse_weight_kg(raw, is_target=(current_target == "weight_kg"))
        if value is not None:
            result["weight_kg"] = value

    return result
