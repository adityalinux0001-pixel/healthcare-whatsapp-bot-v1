"""Deterministic checks applied after model-generated daily plans."""
from __future__ import annotations

import re

from app.llm.schemas import DietPlanOutput

_MEAL_FIELDS = (
    "breakfast",
    "mid_morning_snack",
    "lunch",
    "evening_snack",
    "dinner",
)

_DIET_FORBIDDEN = {
    "veg": {
        "chicken", "mutton", "lamb", "beef", "pork", "fish", "prawn", "prawns",
        "shrimp", "seafood", "meat", "chicken tikka", "fish curry", "egg", "eggs",

    },
    "eggetarian": {
        "chicken", "mutton", "lamb", "beef", "pork", "fish", "prawn", "prawns",
        "shrimp", "seafood", "meat",
    },
    "vegan": {
        "milk", "curd", "yogurt", "paneer", "cheese", "butter", "ghee",
        "cream", "whey", "egg", "eggs", "anda", "ande", "chicken", "mutton", "fish",
        "prawn", "prawns", "shrimp", "seafood", "meat",
    },
}

_ALLERGY_ALIASES = {
    "peanut": {"peanut", "groundnut"},
    "milk": {"milk", "dairy", "curd", "yogurt", "paneer", "cheese", "butter", "ghee"},
    "egg": {"egg", "eggs"},
    "soy": {"soy", "soya", "soybean", "tofu"},
    "wheat": {"wheat", "flour", "all-purpose flour"},
    "gluten": {"gluten", "wheat", "flour", "all-purpose flour"},
}


def _contains_term(text: str, term: str) -> bool:
    term = term.strip().casefold()
    if not term:
        return False
    if re.search(r"[^a-z0-9\s-]", term):
        return term in text
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text))


def _expanded_terms(csv_text: str | None) -> set[str]:
    if not csv_text or csv_text == "None reported":
        return set()
    terms: set[str] = set()
    for raw in re.split(r"[,;|]", csv_text):
        item = raw.strip().casefold()
        if not item:
            continue
        terms.add(item)
        for aliases in _ALLERGY_ALIASES.values():
            if item in aliases:
                terms.update(aliases)
    return terms


def validate_plan(plan: DietPlanOutput, profile: dict) -> list[str]:
    problems: list[str] = []
    if not plan.breakfast.strip() or not plan.lunch.strip() or not plan.dinner.strip() or not plan.exercise.strip():
        problems.append("missing required plan section")

    meal_text = "\n".join(getattr(plan, field) for field in _MEAL_FIELDS).casefold()

    diet = str(profile.get("diet_preference") or "").casefold()
    for forbidden in _DIET_FORBIDDEN.get(diet, set()):
        if _contains_term(meal_text, forbidden):
            problems.append(f"diet preference conflict: {forbidden}")

    for term in _expanded_terms(profile.get("allergies")):
        if _contains_term(meal_text, term):
            problems.append(f"allergy conflict: {term}")

    for term in _expanded_terms(profile.get("food_dislikes")):
        if _contains_term(meal_text, term):
            problems.append(f"food dislike conflict: {term}")

    return problems
