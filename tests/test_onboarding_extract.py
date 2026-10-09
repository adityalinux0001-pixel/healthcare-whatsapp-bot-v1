"""Hair-loss onboarding regressions: deterministic parsing and conditional fields."""
from app.models import User
from app.services.onboarding_extract import ONBOARDING_ORDER, QUESTIONS, RETRY_HINTS, extract_fields_from_text


def _user_from_profile(profile: dict) -> User:
    user = User(phone_number="test")
    for field, value in profile.items():
        setattr(user, field, value)
    return user


def _run(turns: list[str]) -> tuple[dict, list[str]]:
    profile: dict = {}
    for text in turns:
        user = _user_from_profile(profile)
        missing = user.missing_fields()
        if not missing:
            break
        target = missing[0]
        profile.update(extract_fields_from_text(text, target, missing, history=[]))
    return profile, _user_from_profile(profile).missing_fields()


def test_name_is_never_asked_or_collected():
    assert "name" not in ONBOARDING_ORDER
    assert "name" not in QUESTIONS
    assert "name" not in RETRY_HINTS
    assert ONBOARDING_ORDER[0] == "age"


def test_adult_happy_path_completes_with_conditional_family_relation():
    profile, missing = _run([
        "28, Indore", "2", "3", "170 cm, 65 kg", "4",
        "prefer not to say", "1", "father", "3",
    ])
    assert missing == []
    assert profile == {
        "age": 28, "city": "Indore", "hair_wash_frequency": "2_3_times_week",
        "water_hardness": "hard", "height_cm": 170.0, "weight_kg": 65.0,
        "sugary_food_drink_intake": "high", "sexually_active": "prefer_not_to_say",
        "family_hair_loss": "yes", "family_hair_loss_relation": "father",
        "dairy_intake": "moderate",
    }


def test_minor_is_not_asked_sexual_activity_question():
    user = User(phone_number="minor", age=17)
    assert "sexually_active" not in user.missing_fields()
    profile, missing = _run(["17, Pune", "1", "5", "155 cm, 48 kg", "2", "2", "3"])
    assert profile["age"] == 17
    assert profile["family_hair_loss"] == "no"
    assert "sexually_active" not in profile
    assert "family_hair_loss_relation" not in profile
    assert missing == []


def test_family_relation_is_required_only_when_history_is_yes():
    no_profile, no_missing = _run(["30, Delhi", "1", "1", "170 cm 70 kg", "3", "no", "2"])
    assert no_profile["family_hair_loss"] == "no"
    assert "family_hair_loss_relation" not in no_profile
    assert no_missing == []

    yes_profile, yes_missing = _run(["30, Delhi", "1", "1", "170 cm 70 kg", "3", "yes"])
    assert yes_profile["family_hair_loss"] == "yes"
    assert yes_missing[0] == "family_hair_loss_relation"


def test_menu_numbers_apply_only_to_current_field():
    assert extract_fields_from_text("3", "hair_wash_frequency", ["hair_wash_frequency"]) == {"hair_wash_frequency": "once_week"}
    assert extract_fields_from_text("3", "water_hardness", ["water_hardness"]) == {"water_hardness": "hard"}
    assert extract_fields_from_text("3", "sugary_food_drink_intake", ["sugary_food_drink_intake"]) == {"sugary_food_drink_intake": "moderate"}
    assert extract_fields_from_text("3", "dairy_intake", ["dairy_intake"]) == {"dairy_intake": "moderate"}


def test_combined_height_weight_and_common_units():
    extracted = extract_fields_from_text("5 ft 7, 143 lbs", "height_cm", ["height_cm", "weight_kg"])
    assert extracted["height_cm"] == round((5 * 12 + 7) * 2.54, 2)
    assert extracted["weight_kg"] == round(143 * 0.453592, 2)


def test_invalid_current_answer_does_not_advance():
    profile, missing = _run(["not an age", "vegetarian"])
    assert "age" not in profile
    assert missing[0] == "age"


def test_every_question_has_a_deterministic_retry_hint():
    for field in ONBOARDING_ORDER:
        assert field in QUESTIONS
        assert field in RETRY_HINTS
        # Initial questions preserve the manager-approved wording/options;
        # formatting or answer-help text is reserved for invalid/partial replies.
        assert QUESTIONS[field].strip()
        assert "Please" in RETRY_HINTS[field] or "choose" in RETRY_HINTS[field].casefold()
