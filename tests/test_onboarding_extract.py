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
    # order: ... sugar, sexually_active, family_hair_loss, (relation if yes), dairy
    no_profile, no_missing = _run(["30, Delhi", "1", "1", "170 cm 70 kg", "3", "no", "no", "2"])
    assert no_profile["family_hair_loss"] == "no"
    assert "family_hair_loss_relation" not in no_profile
    assert no_missing == []

    yes_profile, yes_missing = _run(["30, Delhi", "1", "1", "170 cm 70 kg", "3", "no", "yes"])
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


# --------------------------------------------------------------------------
# Strict-parsing regressions: one reply must only ever fill the question that
# is being asked, and unclear input must never reach the database.
# --------------------------------------------------------------------------
import pytest


def _only(field: str, text: str, missing: list[str] | None = None) -> dict:
    return extract_fields_from_text(text, field, missing or ONBOARDING_ORDER[ONBOARDING_ORDER.index(field):])


@pytest.mark.parametrize("field,text,expected_keys", [
    ("hair_wash_frequency", "Daily", {"hair_wash_frequency"}),       # must NOT fill dairy
    ("sugary_food_drink_intake", "Low", {"sugary_food_drink_intake"}),  # must NOT fill dairy
    ("sugary_food_drink_intake", "None", {"sugary_food_drink_intake"}),  # must NOT fill dairy/family
    ("sexually_active", "No", {"sexually_active"}),                  # must NOT fill family history
    ("water_hardness", "I'm not sure", {"water_hardness"}),          # must NOT fill family history
])
def test_a_reply_never_fills_a_different_field(field, text, expected_keys):
    assert set(_only(field, text)) == expected_keys


def test_full_option_text_is_accepted_for_every_menu():
    assert _only("hair_wash_frequency", "2–3 times a week") == {"hair_wash_frequency": "2_3_times_week"}
    assert _only("hair_wash_frequency", "Less than once a week") == {"hair_wash_frequency": "less_than_once_week"}
    assert _only("water_hardness", "Moderately hard water") == {"water_hardness": "moderately_hard"}
    assert _only("water_hardness", "Very hard water") == {"water_hardness": "very_hard"}
    assert _only("sugary_food_drink_intake", "None or very little") == {"sugary_food_drink_intake": "none_or_very_little"}
    assert _only("dairy_intake", "Low — occasionally") == {"dairy_intake": "low"}
    assert _only("dairy_intake", "High — 2–3 times a day") == {"dairy_intake": "high"}
    assert _only("dairy_intake", "Very high — more than 3 times a day") == {"dairy_intake": "very_high"}
    assert _only("family_hair_loss_relation", "Brother/Sister") == {"family_hair_loss_relation": "sibling"}
    assert _only("family_hair_loss_relation", "Multiple family members") == {"family_hair_loss_relation": "multiple"}


@pytest.mark.parametrize("field,text", [
    ("hair_wash_frequency", "6"), ("hair_wash_frequency", "0"), ("water_hardness", "7"),
    ("dairy_intake", "0"), ("sexually_active", "4"), ("family_hair_loss", "9"),
    ("hair_wash_frequency", "not daily"),            # negation must not become "daily"
    ("hair_wash_frequency", "daily or once a week"),  # two options -> ambiguous
    ("dairy_intake", "daily"),                        # could be 1x or 3x a day -> ask again
    ("sexually_active", "I have no idea"),            # must not be read as "no"
    ("family_hair_loss", "my father"),                # not a yes/no/not-sure answer
])
def test_unclear_or_out_of_range_menu_answers_are_rejected(field, text):
    assert _only(field, text) == {}


def test_family_history_yes_with_relative_and_ambiguous_relative():
    assert _only("family_hair_loss", "yes, father") == {
        "family_hair_loss": "yes", "family_hair_loss_relation": "father"}
    assert _only("family_hair_loss", "1") == {"family_hair_loss": "yes"}
    assert _only("family_hair_loss", "yes, father and brother") == {"family_hair_loss": "yes"}


@pytest.mark.parametrize("text,expected", [
    ("28, Indore", (28, "Indore")),
    ("28 Indore", (28, "Indore")),
    ("Indore, 28", (28, "Indore")),
    ("Age: 28 City: Indore", (28, "Indore")),
    ("28 years, New Delhi", (28, "New Delhi")),
    ("28", (28, None)),
])
def test_age_and_city_formats(text, expected):
    result = _only("age", text, ["age", "city"])
    assert (result.get("age"), result.get("city")) == expected


@pytest.mark.parametrize("text", ["Indore", "yes", "abc", "0", "121", "28.5, Indore", "twenty eight"])
def test_bad_age_replies_store_nothing(text):
    assert _only("age", text, ["age", "city"]) == {}


@pytest.mark.parametrize("text", ["yes", "no", "I dont know", "ok", "123", "28", "Indore 452001", "x"])
def test_city_rejects_junk(text):
    assert _only("city", text, ["city"]) == {}


def test_city_accepts_real_names_and_strips_sentence_prefix():
    assert _only("city", "Indore", ["city"]) == {"city": "Indore"}
    assert _only("city", "I live in Navi Mumbai", ["city"]) == {"city": "Navi Mumbai"}
    assert _only("city", "Indore, Madhya Pradesh", ["city"]) == {"city": "Indore, Madhya Pradesh"}


def test_weight_requires_a_unit_and_never_guesses_kg_vs_lbs():
    assert _only("weight_kg", "150", ["weight_kg"]) == {}
    assert _only("weight_kg", "65 kg", ["weight_kg"]) == {"weight_kg": 65.0}
    assert _only("weight_kg", "143 lbs", ["weight_kg"]) == {"weight_kg": round(143 * 0.453592, 2)}


def test_height_and_weight_formats():
    both = ["height_cm", "weight_kg"]
    assert _only("height_cm", "170 cm, 65 kg", both) == {"height_cm": 170.0, "weight_kg": 65.0}
    assert _only("height_cm", "5 ft 7 in, 143 lbs", both) == {
        "height_cm": round(67 * 2.54, 2), "weight_kg": round(143 * 0.453592, 2)}
    assert _only("height_cm", "5'7\", 70 kg", both)["height_cm"] == round(67 * 2.54, 2)
    assert _only("height_cm", "170 cm", both) == {"height_cm": 170.0}   # weight is asked next
    assert _only("height_cm", "170", both) == {"height_cm": 170.0}
    assert _only("height_cm", "170, 65 kg", both) == {"height_cm": 170.0, "weight_kg": 65.0}


@pytest.mark.parametrize("text", ["5.7 ft", "5 ft 15 in", "1700 cm", "50 cm", "65 kg", "170 65", "tall"])
def test_bad_height_replies_store_nothing(text):
    assert _only("height_cm", text, ["height_cm", "weight_kg"]) == {}


def test_every_menu_question_is_numbered_and_tells_the_user_how_to_reply():
    for field in ONBOARDING_ORDER:
        text = QUESTIONS[field]
        assert "Example" in text or "Examples" in text
    for field in ("hair_wash_frequency", "water_hardness", "sugary_food_drink_intake",
                  "sexually_active", "family_hair_loss", "family_hair_loss_relation", "dairy_intake"):
        assert "1. " in QUESTIONS[field] and "only the number" in QUESTIONS[field]


def test_full_flow_with_text_answers_never_skips_or_prefills_a_question():
    # Typed option text instead of numbers: every question must still be asked in order.
    turns = ["28, Indore", "Daily", "Hard water", "170 cm, 65 kg", "Low", "No", "No", "Low — occasionally"]
    profile, missing = _run(turns)
    assert missing == []
    assert profile["hair_wash_frequency"] == "daily"
    assert profile["sugary_food_drink_intake"] == "low"
    assert profile["sexually_active"] == "no"
    assert profile["family_hair_loss"] == "no"
    assert profile["dairy_intake"] == "low"