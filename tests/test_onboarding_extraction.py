"""Regression tests for the deterministic onboarding-extraction fallbacks.

These target two production bugs reported from real WhatsApp transcripts:

1. Bare-word replies to "what's your name" were sometimes not recognized at all
   (the question looped), and worse, several rejected/typo'd name attempts across
   turns were sometimes merged by the model into one garbled name (e.g.
   "nilesh" + "ritesh" + "bhushan" -> "Nileshjriteshbhushan").
2. Casual negative replies to "Do you have any allergies or medical conditions?"
   (anything other than an exact "no"/"none"/"nothing to report") were not
   recognized, so the bot kept re-asking the same question indefinitely.
"""

from app.services.conversation_service import (_deterministic_health_answers, _deterministic_profile_hints,
_deterministic_health_answers, 
)

ALLERGY_AND_MEDICAL_QUESTION = (
    "Do you have any allergies or medical conditions? If none, just say no or none."
)
NAME_QUESTION = "Let's complete your profile first. Please tell me your name 😊"


def _history_with_assistant(message: str) -> list[dict]:
    return [{"role": "assistant", "content": message}]


# ---------------------------------------------------------------------------
# Allergy / medical-conditions denial loop
# ---------------------------------------------------------------------------

class TestAllergyMedicalDenial:
    def test_bare_no_resolves_both_fields(self):
        history = _history_with_assistant(ALLERGY_AND_MEDICAL_QUESTION)
        result = _deterministic_health_answers(
            "No", ["allergies", "medical_conditions"], history
        )
        assert result == {"allergies": "None reported", "medical_conditions": "None reported"}

    def test_casual_sentence_without_the_word_medical_resolves_both(self):
        # This exact phrasing looped in production: "conditions" alone (no
        # "medical condition") never matched the old per-field regex.
        history = _history_with_assistant(ALLERGY_AND_MEDICAL_QUESTION)
        result = _deterministic_health_answers(
            "I dont have any allergies and conditions",
            ["allergies", "medical_conditions"],
            history,
        )
        assert result == {"allergies": "None reported", "medical_conditions": "None reported"}

    def test_no_i_dont_have_resolves_both(self):
        history = _history_with_assistant(ALLERGY_AND_MEDICAL_QUESTION)
        result = _deterministic_health_answers(
            "No i dont have", ["allergies", "medical_conditions"], history
        )
        assert result == {"allergies": "None reported", "medical_conditions": "None reported"}

    def test_explicit_and_medical_conditions_phrasing(self):
        history = _history_with_assistant(ALLERGY_AND_MEDICAL_QUESTION)
        result = _deterministic_health_answers(
            "I dont have any allergies and medical conditions",
            ["allergies", "medical_conditions"],
            history,
        )
        assert result == {"allergies": "None reported", "medical_conditions": "None reported"}

    def test_single_remaining_field_only_sets_that_field(self):
        history = _history_with_assistant("Do you have any medical conditions?")
        result = _deterministic_health_answers(
            "no i dont have", ["medical_conditions"], history
        )
        assert result == {"medical_conditions": "None reported"}
        assert "allergies" not in result

    def test_does_not_treat_a_real_report_as_a_denial(self):
        history = _history_with_assistant(ALLERGY_AND_MEDICAL_QUESTION)
        result = _deterministic_health_answers(
            "No allergies, but I have mild asthma",
            ["allergies", "medical_conditions"],
            history,
        )
        # allergies is a clean denial; medical_conditions must NOT be guessed here —
        # that requires real semantic understanding, left to the LLM.
        assert result.get("allergies") == "None reported"
        assert "medical_conditions" not in result

    def test_does_not_fire_without_relevant_question_context(self):
        # If the previous assistant turn wasn't about allergies/medical conditions,
        # a bare "no" must not be interpreted as health data.
        history = _history_with_assistant("What is your fitness goal?")
        result = _deterministic_health_answers(
            "no", ["allergies", "medical_conditions"], history
        )
        assert result == {}

    def test_already_answered_field_is_not_reset(self):
        # Only fields still in missing_fields should ever be touched.
        history = _history_with_assistant(ALLERGY_AND_MEDICAL_QUESTION)
        result = _deterministic_health_answers("no", ["allergies"], history)
        assert result == {"allergies": "None reported"}
        assert "medical_conditions" not in result


# ---------------------------------------------------------------------------
# Name extraction / no cross-turn merging
# ---------------------------------------------------------------------------

class TestNameExtraction:
    def test_bare_word_reply_is_captured_as_name(self):
        history = _history_with_assistant(NAME_QUESTION)
        result = _deterministic_profile_hints("XYZ", history, ["name"])
        assert result["name"] == "Xyz"

    def test_each_turn_is_independent_never_merged_with_history(self):
        # Simulates the real transcript: three separate single-word replies to a
        # still-unanswered name question. Each call must only ever see its own
        # current message and must never combine with the words seen in a
        # previous call.
        history = _history_with_assistant(NAME_QUESTION)
        first = _deterministic_profile_hints("nilesjh", history, ["name"])
        second = _deterministic_profile_hints("ritesh", history, ["name"])
        third = _deterministic_profile_hints("bhushan", history, ["name"])
        assert first["name"] == "Nilesjh"
        assert second["name"] == "Ritesh"
        assert third["name"] == "Bhushan"
        # None of them should ever contain another candidate's text.
        for res, other_words in (
            (first, ["ritesh", "bhushan"]),
            (second, ["nilesjh", "bhushan"]),
            (third, ["nilesjh", "ritesh"]),
        ):
            for word in other_words:
                assert word not in res["name"].casefold()

    def test_does_not_fire_once_name_already_known(self):
        history = _history_with_assistant(NAME_QUESTION)
        result = _deterministic_profile_hints("ritesh", history, [])  # name no longer missing
        assert "name" not in result

    def test_does_not_capture_yes_no_or_greetings_as_name(self):
        history = _history_with_assistant(NAME_QUESTION)
        for reply in ["yes", "no", "hi", "hello", "ok"]:
            result = _deterministic_profile_hints(reply, history, ["name"])
            assert "name" not in result, f"{reply!r} should not be captured as a name"

    def test_does_not_fire_without_a_name_question_in_context(self):
        history = _history_with_assistant("What is your age and gender?")
        result = _deterministic_profile_hints("bhushan", history, ["name"])
        assert "name" not in result

    def test_explicit_my_name_is_phrasing_still_works(self):
        history = _history_with_assistant(NAME_QUESTION)
        result = _deterministic_profile_hints("my name is Ritesh Kumar", history, ["name"])
        assert result["name"] == "Ritesh Kumar"

    def test_compound_message_with_digits_is_left_to_the_llm(self):
        # "bhushan male 39" is intentionally NOT handled by the bare-word
        # fallback (it contains digits) — that combined pattern is left to the
        # semantic extractor, matching existing behavior for age/gender.
        history = _history_with_assistant(NAME_QUESTION)
        result = _deterministic_profile_hints("bhushan male 39", history, ["name"])
        assert "name" not in result
        # but age/gender deterministic extraction still works from the same message
        assert result.get("gender") == "male"
        assert result.get("age") == 39