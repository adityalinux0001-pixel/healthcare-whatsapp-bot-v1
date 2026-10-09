from __future__ import annotations

import re
from typing import Any

_ALLOWED_ACTIVITY = {"sedentary", "light", "moderate", "active"}
_ALLOWED_GOALS = {"weight_loss", "weight_gain", "maintain", "muscle_gain"}
_ALLOWED_DIETS = {"veg", "non_veg", "eggetarian", "vegan"}
_ALLOWED_WASH_FREQUENCY = {"daily", "2_3_times_week", "once_week", "less_than_once_week"}
_ALLOWED_WATER_HARDNESS = {"soft", "moderately_hard", "hard", "very_hard", "not_sure"}
_ALLOWED_SUGAR_INTAKE = {"none_or_very_little", "low", "moderate", "high", "very_high"}
_ALLOWED_SEXUALLY_ACTIVE = {"yes", "no", "prefer_not_to_say"}
_ALLOWED_FAMILY_HAIR_LOSS = {"yes", "no", "not_sure"}
_ALLOWED_FAMILY_RELATION = {"father", "mother", "sibling", "grandparent", "multiple", "other"}
_ALLOWED_DAIRY_INTAKE = {"none", "low", "moderate", "high", "very_high"}

_ACTIVITY_ALIASES = {
    "sedentary": "sedentary",
    "sedentry": "sedentary",
    "sedentary lifestyle": "sedentary",
    "inactive": "sedentary",
    "not active": "sedentary",
    "no exercise": "sedentary",
    "no workout": "sedentary",
    "light": "light",
    "lightly active": "light",
    "moderate": "moderate",
    "moderately active": "moderate",
    "active": "active",
    "very active": "active",
}
_GOAL_ALIASES = {
    "lose weight": "weight_loss",
    "lose fat": "weight_loss",
    "fat loss": "weight_loss",
    "weight loss": "weight_loss",
    "fat loss": "weight_loss",
    "weight_loss": "weight_loss",
    "gain weight": "weight_gain",
    "gain some weight": "weight_gain",
    "weight gain": "weight_gain",
    "maintain": "maintain",
    "maintenance": "maintain",
    "muscle gain": "muscle_gain",
    "gain muscle": "muscle_gain",
    "gain muscles": "muscle_gain",
    "build muscle": "muscle_gain",
    "build muscles": "muscle_gain",
    "put on muscle": "muscle_gain",
    "muscle_gain": "muscle_gain",
}
_DIET_ALIASES = {
    "veg": "veg",
    "vegetarian": "veg",
    "vegeterian": "veg",
    "vegitarian": "veg",
    "vegetarian diet": "veg",
    "non veg": "non_veg",
    "non-veg": "non_veg",
    "non vegetarian": "non_veg",
    "non_vegetarian": "non_veg",
    "eggetarian": "eggetarian",
    "eggitarian": "eggetarian",
    "vegan": "vegan",
}

_GENDER_ALIASES = {
    "male": "male",
    "man": "male",
    "m": "male",
    "boy": "male",
    "female": "female",
    "woman": "female",
    "f": "female",
    "girl": "female",
}

_TEXT_LIMITS = {
    "name": 100,
    "city": 100,
    "gender": 30,
    "hair_wash_frequency": 40,
    "water_hardness": 40,
    "sugary_food_drink_intake": 40,
    "sexually_active": 30,
    "family_hair_loss": 20,
    "family_hair_loss_relation": 50,
    "dairy_intake": 40,
    "allergies": 1000,
    "medical_conditions": 1500,
    "food_dislikes": 1000,
}

_HAIR_ENUMS = {
    "hair_wash_frequency": ({
        "everyday": "daily", "every day": "daily", "2-3 times a week": "2_3_times_week",
        "2–3 times a week": "2_3_times_week", "twice a week": "2_3_times_week",
        "2 to 3 times a week": "2_3_times_week", "once a week": "once_week",
        "less than once a week": "less_than_once_week",
    }, _ALLOWED_WASH_FREQUENCY),
    "water_hardness": ({
        "soft water": "soft", "moderately hard water": "moderately_hard",
        "hard water": "hard", "very hard water": "very_hard", "unsure": "not_sure",
        "i'm not sure": "not_sure", "i am not sure": "not_sure", "i don't know": "not_sure",
    }, _ALLOWED_WATER_HARDNESS),
    "sugary_food_drink_intake": ({
        "none": "none_or_very_little", "none or very little": "none_or_very_little",
        "very little": "none_or_very_little", "very high": "very_high", "medium": "moderate",
    }, _ALLOWED_SUGAR_INTAKE),
    "sexually_active": ({
        "y": "yes", "n": "no", "prefer not to say": "prefer_not_to_say",
        "prefer not": "prefer_not_to_say", "rather not say": "prefer_not_to_say",
    }, _ALLOWED_SEXUALLY_ACTIVE),
    "family_hair_loss": ({"unsure": "not_sure", "maybe": "not_sure", "not sure": "not_sure"}, _ALLOWED_FAMILY_HAIR_LOSS),
    "family_hair_loss_relation": ({
        "dad": "father", "mom": "mother", "mum": "mother", "brother": "sibling",
        "sister": "sibling", "brother/sister": "sibling", "brother or sister": "sibling",
        "sibling": "sibling", "grandparents": "grandparent",
        "grandmother": "grandparent", "grandfather": "grandparent",
        "multiple family members": "multiple", "both parents": "multiple", "someone else": "other",
    }, _ALLOWED_FAMILY_RELATION),
    "dairy_intake": ({
        "no dairy": "none", "occasionally": "low", "sometimes": "low", "once a day": "moderate",
        "daily": "moderate", "2-3 times a day": "high", "2 to 3 times a day": "high",
        "very high": "very_high", "more than 3 times a day": "very_high",
    }, _ALLOWED_DAIRY_INTAKE),
}


def _clean_text(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None
    return text[:limit]


def _normalize_list_text(value: Any, limit: int) -> str | None:
    text = _clean_text(value, limit)
    if not text:
        return None
    items = [re.sub(r"\s+", " ", item).strip() for item in re.split(r"[,;|]", text)]
    items = [item for item in items if item]
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = item.casefold()
        if key not in seen:
            result.append(item)
            seen.add(key)
    return ", ".join(result)[:limit] if result else None


def _normalize_enum(value: Any, aliases: dict[str, str], allowed: set[str]) -> str | None:
    text = _clean_text(value, 50)
    if not text:
        return None
    key = text.casefold()
    normalized = aliases.get(key, key.replace("-", "_"))
    return normalized if normalized in allowed else None


def validate_extracted_fields(extracted: dict[str, Any]) -> dict[str, Any]:
    """Return only safe, normalized fields suitable for persistence."""
    clean: dict[str, Any] = {}

    if "name" in extracted:
        value = _clean_text(extracted["name"], _TEXT_LIMITS["name"])
        if value:
            clean["name"] = value

    if "age" in extracted:
        try:
            age = int(extracted["age"])
        except (TypeError, ValueError):
            age = None
        # Preserve minors so onboarding can explicitly refuse automated adult plans.
        if age is not None and 1 <= age <= 120:
            clean["age"] = age

    if "gender" in extracted:
        value = _clean_text(extracted["gender"], _TEXT_LIMITS["gender"])
        if value:
            gender_key = value.casefold()
            clean["gender"] = _GENDER_ALIASES.get(gender_key, value)

    for field, low, high in (
        ("height_cm", 100.0, 250.0),
        ("weight_kg", 20.0, 350.0),
    ):
        if field in extracted:
            try:
                number = float(extracted[field])
            except (TypeError, ValueError):
                number = None
            if number is not None and low <= number <= high:
                clean[field] = round(number, 2)

    if "city" in extracted:
        value = _clean_text(extracted["city"], _TEXT_LIMITS["city"])
        if value and value.casefold() not in {"skip", "prefer not to say"}:
            clean["city"] = value

    for field, (aliases, allowed) in _HAIR_ENUMS.items():
        if field in extracted:
            value = _normalize_enum(extracted[field], aliases, allowed)
            if value:
                clean[field] = value

    if "activity_level" in extracted:
        value = _normalize_enum(extracted["activity_level"], _ACTIVITY_ALIASES, _ALLOWED_ACTIVITY)
        if value:
            clean["activity_level"] = value

    if "goal" in extracted:
        value = _normalize_enum(extracted["goal"], _GOAL_ALIASES, _ALLOWED_GOALS)
        if value:
            clean["goal"] = value

    if "diet_preference" in extracted:
        value = _normalize_enum(extracted["diet_preference"], _DIET_ALIASES, _ALLOWED_DIETS)
        if value:
            clean["diet_preference"] = value

    for field in ("allergies", "medical_conditions"):
        if field in extracted:
            raw = _clean_text(extracted[field], _TEXT_LIMITS[field])
            if raw and raw.casefold() in {
                "none", "none reported", "no", "no allergies", "no allergy",
                "no medical conditions", "no medical condition", "no medical issues",
                "no health issues", "no health problems", "nothing", "nothing to report",
                "nothing that i know of", "i don't have any", "i dont have any",
                "i do not have any", "i have none", "there are none", "there is none",
            }:
                clean[field] = "None reported"
            else:
                value = _normalize_list_text(extracted[field], _TEXT_LIMITS[field])
                if value:
                    clean[field] = value

    if "food_dislikes" in extracted:
        value = _normalize_list_text(extracted["food_dislikes"], _TEXT_LIMITS["food_dislikes"])
        if value:
            clean["food_dislikes"] = value

    return clean