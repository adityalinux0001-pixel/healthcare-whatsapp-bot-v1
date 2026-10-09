
from __future__ import annotations

import re
from typing import Any

ONBOARDING_ORDER: list[str] = [
    "age", "city", "hair_wash_frequency", "water_hardness", "height_cm",
    "weight_kg", "sugary_food_drink_intake", "sexually_active",
    "family_hair_loss", "family_hair_loss_relation", "dairy_intake",
]


def _menu(title: str, options: list[str], example: str, note: str | None = None) -> str:
    """Question text exactly as approved + numbered options + how-to-reply line."""
    numbers = ", ".join(str(i) for i in range(1, len(options) + 1))
    numbers = numbers.rsplit(", ", 1)
    numbers_text = " or ".join(numbers) if len(numbers) == 2 else numbers[0]
    lines = [f"*{title}*", ""]
    lines += [f"{i}. {option}" for i, option in enumerate(options, 1)]
    if note:
        lines += ["", f"_{note}_"]
    lines += ["", f"👉 Reply with *only the number* ({numbers_text}). Example: {example}"]
    return "\n".join(lines)


QUESTIONS: dict[str, str] = {
    # Question wording/options below are the manager-approved text. Only the
    # numbering and the "how to reply" line were added to prevent wrong input.
    "age": (
        "*How old are you, and where do you currently live?*\n\n"
        "- Age: ___\n\n- City: ___\n\n"
        "👉 Reply in *one message*: age in numbers, then your city name.\n"
        "Example: 28, Indore"
    ),
    "city": (
        "*Where do you currently live?*\n\n"
        "- City: ___\n\n"
        "👉 Reply with *only your city name*. Example: Indore"
    ),
    "hair_wash_frequency": _menu(
        "How often do you wash your hair?",
        ["Daily", "2–3 times a week", "Once a week", "Less than once a week"],
        example="2",
    ),
    "water_hardness": _menu(
        "What type of water do you usually use to wash your hair?",
        ["Soft water", "Moderately hard water", "Hard water", "Very hard water", "I'm not sure"],
        example="5",
    ),
    "height_cm": (
        "*What are your current height and weight?*\n\n"
        "- Height: ___ cm / ft & inches\n- Weight: ___ kg / lbs\n\n"
        "👉 Reply in *one message* and *always write the units*. Examples:\n"
        "170 cm, 65 kg\n"
        "5 ft 7 in, 143 lbs"
    ),
    "weight_kg": (
        "*What is your current weight?*\n\n"
        "- Weight: ___ kg / lbs\n\n"
        "👉 Reply with the number *and the unit*. Examples: 65 kg or 143 lbs"
    ),
    "sugary_food_drink_intake": _menu(
        "How much sugary food or drinks do you normally consume?",
        ["None or very little", "Low", "Moderate", "High", "Very high"],
        example="3",
        note="Examples: sweets, desserts, sugary tea/coffee, soft drinks, packaged juices, etc.",
    ),
    "sexually_active": _menu(
        "Are you currently sexually active?",
        ["Yes", "No", "Prefer not to say"],
        example="3",
    ),
    "family_hair_loss": _menu(
        "Does hair loss run in your family history ?",
        ["Yes", "No", "Not sure"],
        example="2",
    ),
    "family_hair_loss_relation": _menu(
        "Who in your family has experienced noticeable hair loss?",
        ["Father", "Mother", "Brother/Sister", "Grandparent", "Multiple family members", "Other"],
        example="1",
    ),
    "dairy_intake": _menu(
        "How much dairy do you normally consume?",
        [
            "None",
            "Low — occasionally",
            "Moderate — once a day",
            "High — 2–3 times a day",
            "Very high — more than 3 times a day",
        ],
        example="3",
        note="Examples: milk, curd/yogurt, paneer, cheese, butter, cream, etc.",
    ),
}

RETRY_HINTS: dict[str, str] = {
    "age": (
        "Sorry, I couldn't read that. Please send your age (in numbers) and city in one message. "
        "*Example:* 28, Indore"
    ),
    "city": "Sorry, I couldn't read that. Please send only your city name. *Example:* Indore",
    "hair_wash_frequency": "Sorry, I couldn't read that. Please reply with only 1, 2, 3 or 4. *Example:* 2",
    "water_hardness": "Sorry, I couldn't read that. Please reply with only 1, 2, 3, 4 or 5. *Example:* 5",
    "height_cm": (
        "Sorry, I couldn't read that. Please send height and weight *with units*. "
        "*Example:* 170 cm, 65 kg  or  5 ft 7 in, 143 lbs"
    ),
    "weight_kg": (
        "Sorry, I couldn't read that. Please send your weight *with the unit* (kg or lbs). "
        "*Example:* 65 kg"
    ),
    "sugary_food_drink_intake": "Sorry, I couldn't read that. Please reply with only 1, 2, 3, 4 or 5. *Example:* 3",
    "sexually_active": "Sorry, I couldn't read that. Please reply with only 1, 2 or 3. *Example:* 3",
    "family_hair_loss": "Sorry, I couldn't read that. Please reply with only 1, 2 or 3. *Example:* 2",
    "family_hair_loss_relation": (
        "Sorry, I couldn't read that. Please reply with only 1, 2, 3, 4, 5 or 6. *Example:* 1"
    ),
    "dairy_intake": "Sorry, I couldn't read that. Please reply with only 1, 2, 3, 4 or 5. *Example:* 3",
}

# NOTE: aliases are written with plain "-" because _fold() converts en/em dashes.
_WASH = {
    "daily": "daily", "every day": "daily", "everyday": "daily", "1": "daily",
    "2-3 times a week": "2_3_times_week", "2 to 3 times a week": "2_3_times_week",
    "twice a week": "2_3_times_week", "2-3 times per week": "2_3_times_week", "2": "2_3_times_week",
    "once a week": "once_week", "one time a week": "once_week", "weekly": "once_week", "3": "once_week",
    "less than once a week": "less_than_once_week", "less than once weekly": "less_than_once_week",
    "less than weekly": "less_than_once_week", "4": "less_than_once_week",
}
_WATER = {
    "soft": "soft", "soft water": "soft", "1": "soft",
    "moderately hard": "moderately_hard", "moderately hard water": "moderately_hard", "2": "moderately_hard",
    "hard": "hard", "hard water": "hard", "3": "hard",
    "very hard": "very_hard", "very hard water": "very_hard", "4": "very_hard",
    "not sure": "not_sure", "unsure": "not_sure", "i don't know": "not_sure",
    "i dont know": "not_sure", "dont know": "not_sure", "don't know": "not_sure", "5": "not_sure",
}
_SUGAR = {
    "none": "none_or_very_little", "none or very little": "none_or_very_little",
    "very little": "none_or_very_little", "very low": "none_or_very_little", "1": "none_or_very_little",
    "low": "low", "2": "low", "moderate": "moderate", "medium": "moderate", "3": "moderate",
    "high": "high", "4": "high", "very high": "very_high", "5": "very_high",
}
_SEXUALLY_ACTIVE = {
    "yes": "yes", "y": "yes", "yeah": "yes", "yep": "yes", "1": "yes",
    "no": "no", "n": "no", "nope": "no", "nah": "no", "2": "no",
    "prefer not to say": "prefer_not_to_say", "prefer not": "prefer_not_to_say",
    "rather not say": "prefer_not_to_say", "3": "prefer_not_to_say",
}
_FAMILY_HISTORY = {
    "yes": "yes", "y": "yes", "yeah": "yes", "yep": "yes", "1": "yes",
    "no": "no", "n": "no", "nope": "no", "nah": "no", "2": "no",
    "not sure": "not_sure", "unsure": "not_sure", "maybe": "not_sure",
    "don't know": "not_sure", "dont know": "not_sure", "3": "not_sure",
}
_RELATION = {
    "father": "father", "dad": "father", "1": "father",
    "mother": "mother", "mom": "mother", "mum": "mother", "2": "mother",
    "brother": "sibling", "sister": "sibling", "sibling": "sibling", "brother or sister": "sibling",
    "brother/sister": "sibling", "3": "sibling",
    "grandparent": "grandparent", "grandparents": "grandparent", "grandmother": "grandparent",
    "grandfather": "grandparent", "4": "grandparent",
    "multiple family members": "multiple", "many family members": "multiple",
    "both parents": "multiple", "multiple": "multiple", "5": "multiple",
    "other": "other", "someone else": "other", "6": "other",
}
_DAIRY = {
    "none": "none", "no dairy": "none", "1": "none",
    "low": "low", "occasionally": "low", "sometimes": "low", "2": "low",
    "moderate": "moderate", "once a day": "moderate", "3": "moderate",
    "high": "high", "2-3 times a day": "high", "2 to 3 times a day": "high", "4": "high",
    "very high": "very_high", "more than 3 times a day": "very_high",
    "over 3 times a day": "very_high", "5": "very_high",
}

_NEGATION = re.compile(r"(?<!\w)(not|no|never|don't|dont|doesn't|doesnt|without|but)(?!\w)")
_MAX_FREE_TEXT_WORDS = 10


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _fold(value: str) -> str:
    text = _norm(value).casefold()
    for src, dst in (("’", "'"), ("‘", "'"), ("′", "'"), ("“", '"'), ("”", '"'), ("″", '"'),
                     ("–", "-"), ("—", "-")):
        text = text.replace(src, dst)
    return text


def _bare(value: str) -> str:
    return _fold(value).strip(" .!?\t\n")


# --------------------------------------------------------------------------- age + city

_CITY_JUNK = {
    "yes", "no", "ok", "okay", "hi", "hii", "hello", "hey", "na", "n/a", "none", "nil",
    "nothing", "test", "idk", "thanks", "thank you", "not sure", "unsure", "i dont know",
    "i don't know", "dont know", "don't know", "prefer not to say", "daily", "male", "female",
}
_CITY_PREFIX = re.compile(
    r"^(?:and\s+)?(?:i\s+(?:live|stay|am|reside)\s+(?:in|from|at)|i'm\s+from|i\s+am\s+from|from|"
    r"living\s+in|staying\s+in|my\s+city\s+is|city\s+is|city|currently\s+in|currently\s+living\s+in|in|at)"
    r"(?:\s+|\s*[:=\-]\s*)",
    flags=re.I,
)


def _clean_city(value: str) -> str | None:
    """Return a plausible city name or None. Never returns sentences, numbers or junk."""
    city = _norm(value)
    city = _CITY_PREFIX.sub("", city).strip(" .,;:-|/")
    if not city or len(city) > 60:
        return None
    if re.search(r"[\d@#$%^&*()=+\[\]{}<>\\~`!?\"]", city):
        return None
    if len(re.findall(r"[^\W\d_]", city)) < 2:
        return None
    if len(city.split()) > 6:
        return None
    if _fold(city).strip(" .") in _CITY_JUNK:
        return None
    return city


def _parse_age_and_city(raw: str) -> tuple[int | None, str | None]:
    """Parse the combined first answer: '28, Indore' / 'Age: 28 City: Indore' / 'Indore, 28'."""
    text = _norm(raw)
    match = re.search(r"\bage\s*[:=\-]?\s*(\d{1,3})\b(?!\.\d)", text, flags=re.I)
    if not match:
        match = re.search(r"(?<![\d.])(\d{1,3})\s*(?:years?\s*old|years?|yrs?|yo|y/o)\b", text, flags=re.I)
    if not match:
        match = re.match(r"^\s*(\d{1,3})\b(?!\s*\.\d)", text)
    if not match:
        # 'Indore, 28' - trailing age is accepted only after a comma.
        match = re.search(r",\s*(\d{1,3})\s*$", text)
    if not match:
        return None, None
    age = int(match.group(1))
    if not 1 <= age <= 120:
        return None, None
    remainder = f"{text[:match.start()]} {text[match.end():]}"
    return age, _clean_city(remainder) if remainder.strip(" .,;:-|/") else None


def _parse_city(raw: str) -> str | None:
    return _clean_city(raw)


# --------------------------------------------------------------------------- height / weight

def _in_range(value: float, low: float, high: float) -> float | None:
    return round(value, 2) if low <= value <= high else None


def _parse_height_cm(raw: str, allow_bare: bool) -> float | None:
    normalized = _fold(raw)
    match = re.search(r"(?<![\d.])(\d{2,3}(?:\.\d+)?)\s*(?:cms?|centimeters?|centimetres?)\b", normalized)
    if match:
        return _in_range(float(match.group(1)), 100, 250)
    match = re.search(r"(?<![\d.])(\d)\s*(?:'|ft|feet|foot)\s*(\d{1,2})?\s*(?:\"|in|inches)?\b", normalized)
    if match:
        feet, inches = int(match.group(1)), int(match.group(2) or 0)
        if inches > 11:
            return None
        return _in_range((feet * 12 + inches) * 2.54, 100, 250)
    match = re.search(r"(?<![\d.])(1\.\d{1,2})\s*m\b", normalized)
    if match:
        return _in_range(float(match.group(1)) * 100, 100, 250)
    if allow_bare:
        # '170' alone, or '170, 65 kg' (height without unit, weight with unit).
        match = re.match(
            r"^\s*(\d{3}(?:\.\d+)?)\s*(?:[,;/|]\s*)?"
            r"(?=$|\d{2,3}(?:\.\d+)?\s*(?:kgs?|kilograms?|kilos?|lbs?|pounds?)\b)",
            _bare(raw),
        )
        if match:
            return _in_range(float(match.group(1)), 100, 250)
    return None


def _parse_weight_kg(raw: str) -> float | None:
    """Weight is accepted ONLY with a unit; a bare number could be kg or lbs."""
    normalized = _fold(raw)
    match = re.search(r"(?<![\d.])(\d{2,3}(?:\.\d+)?)\s*(?:kgs?|kilograms?|kilos?)\b", normalized)
    if match:
        return _in_range(float(match.group(1)), 20, 350)
    match = re.search(r"(?<![\d.])(\d{2,3}(?:\.\d+)?)\s*(?:lbs?|pounds?)\b", normalized)
    if match:
        return _in_range(float(match.group(1)) * 0.453592, 20, 350)
    return None


# --------------------------------------------------------------------------- menu answers

_NUMBER_REPLY = re.compile(r"^(?:option|opt|number|num|answer|ans)?\s*[:#-]?\s*(\d{1,2})\s*[).:]*$")


def _parse_enum(raw: str, aliases: dict[str, str], *, phrase_fallback: bool = True) -> str | None:
    """Map a reply to exactly one option of the CURRENT question, else None."""
    bare = _bare(raw)
    number = _NUMBER_REPLY.match(bare)
    if number:
        key = number.group(1)
        return aliases.get(key) if key in aliases else None
    if bare in aliases:
        return aliases[bare]
    if not phrase_fallback:
        return None

    normalized = _fold(raw)
    if len(normalized.split()) > _MAX_FREE_TEXT_WORDS:
        return None
    spans: list[tuple[int, int, str]] = []
    for phrase in sorted((k for k in aliases if not k.isdigit()), key=len, reverse=True):
        for match in re.finditer(rf"(?<!\w){re.escape(phrase)}(?!\w)", normalized):
            start, end = match.span()
            # A shorter phrase inside an already matched longer one ("hard" in "very hard") is ignored.
            if any(start >= s and end <= e for s, e, _ in spans):
                continue
            spans.append((start, end, aliases[phrase]))
    if len({value for _, _, value in spans}) != 1:
        return None  # nothing matched, or the reply points to several options
    rest = normalized
    for start, end, _ in sorted(spans, reverse=True):
        rest = rest[:start] + " " + rest[end:]
    if _NEGATION.search(rest):
        return None  # e.g. "not daily" must never be stored as "daily"
    return spans[0][2]


def _first_segment(raw: str) -> tuple[str, str]:
    """Split 'yes, father' into ('yes', 'father')."""
    parts = re.split(r"[,;:]|\s[-–—]\s", _bare(raw), maxsplit=1)
    return parts[0].strip(" .!?"), (parts[1] if len(parts) > 1 else "")


def _parse_family_history(raw: str) -> tuple[str | None, str | None]:
    """Yes/No/Not sure only from a short, exact reply. Optional '... , <relative>' companion."""
    head, tail = _first_segment(raw)
    value = _parse_enum(head, _FAMILY_HISTORY, phrase_fallback=False)
    if value != "yes" or not tail.strip():
        return value, None
    return value, _parse_enum(tail, _RELATION)


def extract_fields_from_text(
    text: str,
    current_target: str,
    missing_fields: list[str],
    history: list[dict] | None = None,
) -> dict[str, Any]:
    """Return validated values for the question being asked (plus its paired fields only)."""
    raw = _norm(text)
    result: dict[str, Any] = {}
    missing = set(missing_fields)
    if current_target not in missing:
        return result

    if current_target == "age":
        age, city = _parse_age_and_city(raw)
        if age is not None:
            result["age"] = age
            if city and "city" in missing:
                result["city"] = city

    elif current_target == "city":
        city = _parse_city(raw)
        if city:
            result["city"] = city
        elif _bare(raw) in {"prefer not to say", "skip"}:
            result["city"] = "Not provided"

    elif current_target == "hair_wash_frequency":
        value = _parse_enum(raw, _WASH)
        if value:
            result["hair_wash_frequency"] = value

    elif current_target == "water_hardness":
        value = _parse_enum(raw, _WATER)
        if value:
            result["water_hardness"] = value

    elif current_target == "height_cm":
        height = _parse_height_cm(raw, allow_bare=True)
        if height is not None:
            result["height_cm"] = height
            weight = _parse_weight_kg(raw)
            if weight is not None and "weight_kg" in missing:
                result["weight_kg"] = weight

    elif current_target == "weight_kg":
        weight = _parse_weight_kg(raw)
        if weight is not None:
            result["weight_kg"] = weight

    elif current_target == "sugary_food_drink_intake":
        value = _parse_enum(raw, _SUGAR)
        if value:
            result["sugary_food_drink_intake"] = value

    elif current_target == "sexually_active":
        value = _parse_enum(raw, _SEXUALLY_ACTIVE, phrase_fallback=False)
        if value:
            result["sexually_active"] = value

    elif current_target == "family_hair_loss":
        value, relation = _parse_family_history(raw)
        if value:
            result["family_hair_loss"] = value
            if relation and "family_hair_loss_relation" in missing:
                result["family_hair_loss_relation"] = relation

    elif current_target == "family_hair_loss_relation":
        value = _parse_enum(raw, _RELATION)
        if value:
            result["family_hair_loss_relation"] = value

    elif current_target == "dairy_intake":
        value = _parse_enum(raw, _DAIRY)
        if value:
            result["dairy_intake"] = value

    return result