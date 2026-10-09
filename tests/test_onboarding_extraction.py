"""Regression coverage for the hair-profile extractor used by production onboarding."""
from app.services.onboarding_extract import extract_fields_from_text
from app.services.profile_validation import validate_extracted_fields


def test_combined_age_and_city_are_captured_on_first_answer():
    raw = extract_fields_from_text("28, Indore", "age", ["age", "city"])
    clean = validate_extracted_fields(raw)
    assert clean == {"age": 28, "city": "Indore"}


def test_family_history_can_include_relation_in_same_answer():
    raw = extract_fields_from_text("yes, father", "family_hair_loss", ["family_hair_loss", "family_hair_loss_relation"])
    clean = validate_extracted_fields(raw)
    assert clean == {"family_hair_loss": "yes", "family_hair_loss_relation": "father"}


def test_prefer_not_to_say_is_a_valid_sensitive_answer():
    clean = validate_extracted_fields(extract_fields_from_text(
        "prefer not to say", "sexually_active", ["sexually_active"]
    ))
    assert clean["sexually_active"] == "prefer_not_to_say"
