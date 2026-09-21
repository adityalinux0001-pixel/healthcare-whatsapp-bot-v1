"""Regression tests for the deterministic, LLM-free onboarding engine.

These specifically cover the failure modes seen in real manager/QA testing
before this rewrite:
  * the allergies/medical-conditions question looping forever because a
    "no" variant didn't match a strict fullmatch regex
  * numbered menu answers ("1".."4") bleeding across activity/goal/diet
    since all three reuse the same digits

Onboarding no longer collects a name — it starts directly at age.
"""

from app.services.onboarding_extract import ONBOARDING_ORDER, QUESTIONS, RETRY_HINTS, extract_fields_from_text


def _run(turns: list[str]) -> tuple[dict, list[str]]:
    """Drive the state machine exactly the way conversation_service does:
    always target the first missing field, apply whatever the message
    yields, and recompute what's still missing."""
    profile: dict = {}
    missing = list(ONBOARDING_ORDER)
    for text in turns:
        if not missing:
            break
        target = missing[0]
        extracted = extract_fields_from_text(text, target, missing, history=[])
        profile.update(extracted)
        missing = [f for f in ONBOARDING_ORDER if f not in profile]
    return profile, missing


def test_name_is_never_asked_or_collected():
    assert "name" not in ONBOARDING_ORDER
    assert "name" not in QUESTIONS
    assert "name" not in RETRY_HINTS
    # Onboarding must start at age now that name has been removed.
    assert ONBOARDING_ORDER[0] == "age"


def test_full_happy_path_completes():
    profile, missing = _run([
        "39", "male", "157cm", "67 kg",
        "3", "4", "1",
        "I dont have any allergies and medical conditions", "No",
    ])
    assert missing == []
    assert profile == {
        "age": 39, "gender": "male",
        "height_cm": 157.0, "weight_kg": 67.0,
        "activity_level": "moderate", "goal": "muscle_gain",
        "diet_preference": "veg",
        "allergies": "None reported", "medical_conditions": "None reported",
    }


def test_menu_numbers_do_not_collide_across_fields():
    # activity_level/goal/diet_preference all use "1".."4" for their own
    # menu. A "3" answering activity_level must not also silently fill in
    # goal or diet_preference.
    profile, missing = _run([
        "25", "m", "170", "70",
        "3",  # activity_level -> moderate
    ])
    assert profile["activity_level"] == "moderate"
    assert "goal" not in profile
    assert "diet_preference" not in profile
    assert missing[0] == "goal"


def test_negative_health_answers_all_resolve_instead_of_looping():
    # Every one of these variants appeared in the manager's transcript and
    # caused the old fullmatch-based regex to keep re-asking.
    variants = [
        "No", "no", "None", "I dont have any allergies and conditions",
        "No i dont have", "I dont have any allergies and medical conditions",
        "i dont have any allergies",
    ]
    for text in variants:
        profile, missing = _run([
            "30", "male", "170", "70", "1", "1", "1", text,
        ])
        assert profile.get("allergies") == "None reported", f"failed for: {text!r}"
        assert missing == ["medical_conditions"]


def test_positive_health_answer_is_stored_verbatim_not_misread_as_negative():
    profile, missing = _run([
        "30", "male", "170", "70", "1", "1", "1",
        "I have a peanut allergy",
    ])
    assert profile["allergies"] == "I have a peanut allergy"


def test_unanswered_current_field_does_not_advance():
    # A reply that doesn't answer the CURRENT question must never be
    # silently accepted just because it looks like data for some other
    # field.
    profile, missing = _run(["not a number", "vegetarian"])
    assert "age" not in profile
    assert missing[0] == "age"


def test_minor_age_is_still_captured_for_downstream_refusal_check():
    profile, missing = _run(["15"])
    assert profile["age"] == 15


def test_height_accepts_feet_inches_and_meters():
    profile, _ = _run(["20", "m", "5 ft 7"])
    assert profile["height_cm"] == round((5 * 12 + 7) * 2.54, 2)

    profile2, _ = _run(["20", "m", "1.70m"])
    assert profile2["height_cm"] == 170.0


def test_weight_accepts_pounds():
    profile, _ = _run(["20", "m", "170", "150 lbs"])
    assert profile["weight_kg"] == round(150 * 0.453592, 2)


def test_other_fields_are_opportunistically_captured_while_target_is_unanswered():
    # A message can answer a LATER field (e.g. gender) even while the bot is
    # still waiting on the CURRENT target (age) — but the target itself must
    # not advance until it specifically has a valid value.
    profile, missing = _run(["male"])
    assert profile.get("gender") == "male"
    assert "age" not in profile
    assert missing[0] == "age"


def test_gender_accepts_numeric_menu_replies_only_when_targeted():
    # Real bug: the gender question shows a "1️⃣ Male / 2️⃣ Female" menu and
    # tells the user to reply "1" or "2", but the parser only recognized
    # the words male/female/man/woman/boy/girl/m/f — a bare "1" or "2" was
    # silently rejected forever. Numbers are gated by is_target (same rule
    # as activity/goal/diet, which reuse the same digits for their own
    # menus) so this can never bleed into an unrelated field.
    profile, missing = _run(["30", "1"])
    assert profile["gender"] == "male"
    assert missing[0] == "height_cm"

    profile2, missing2 = _run(["30", "2"])
    assert profile2["gender"] == "female"
    assert missing2[0] == "height_cm"


def test_every_question_and_retry_hint_includes_a_reply_example():
    # "full proper user friendly" onboarding means the user is never left
    # guessing the expected format — every question and every retry hint
    # must show a concrete example answer.
    for field in ONBOARDING_ORDER:
        assert "Reply like this:" in QUESTIONS[field], f"missing example in QUESTIONS[{field!r}]"
        assert "Reply like this:" in RETRY_HINTS[field], f"missing example in RETRY_HINTS[{field!r}]"