from app.llm.schemas import DietPlanOutput
from app.services.plan_validation import validate_plan


def _plan(**overrides):
    data = {
        "breakfast": "Poha with vegetables",
        "mid_morning_snack": "Fruit",
        "lunch": "Roti, dal and sabzi",
        "evening_snack": "Roasted chana",
        "dinner": "Khichdi and vegetables",
        "exercise": "30 minutes brisk walking",
        "hydration_and_routine": "Drink water regularly and keep a consistent sleep schedule.",
        "safety_note": "General wellness guidance only.",
    }
    data.update(overrides)
    return DietPlanOutput(**data)


def test_veg_plan_rejects_meat_and_egg():
    problems = validate_plan(_plan(dinner="Chicken curry with rice"), {"diet_preference": "veg"})
    assert any("diet preference conflict" in p for p in problems)


def test_peanut_allergy_is_rejected():
    problems = validate_plan(_plan(lunch="Peanut chutney with dosa"), {"diet_preference": "veg", "allergies": "peanut"})
    assert any("allergy conflict" in p for p in problems)


def test_disliked_paneer_is_rejected():
    problems = validate_plan(_plan(dinner="Paneer bhurji"), {"diet_preference": "veg", "food_dislikes": "paneer"})
    assert any("food dislike conflict" in p for p in problems)


def test_allergy_is_rejected():
    from app.llm.schemas import DietPlanOutput
    from app.services.plan_validation import validate_plan
    plan = DietPlanOutput(
        breakfast="Peanut poha", mid_morning_snack="Banana", lunch="Dal roti",
        evening_snack="Fruit", dinner="Vegetable khichdi", exercise="20 min walk",
        hydration_and_routine="Water through the day", safety_note="Stop if unwell."
    )
    problems = validate_plan(plan, {"diet_preference": "veg", "allergies": "peanut", "food_dislikes": "None reported"})
    assert any("allergy" in p.lower() for p in problems)

