from app.services.profile_validation import validate_extracted_fields


def test_profile_values_are_normalized_and_invalid_values_dropped():
    result = validate_extracted_fields({
        "age": 25,
        "height_cm": "172.5",
        "weight_kg": 70,
        "activity_level": "Very Active",
        "goal": "fat loss",
        "diet_preference": "vegetarian",
        "allergies": "none",
        "food_dislikes": "Paneer, oats, paneer",
    })
    assert result["age"] == 25
    assert result["height_cm"] == 172.5
    assert result["activity_level"] == "active"
    assert result["goal"] == "weight_loss"
    assert result["diet_preference"] == "veg"
    assert result["allergies"] == "None reported"
    assert result["food_dislikes"] == "Paneer, oats"
    assert "weight_kg" in result


def test_minor_age_is_preserved_for_explicit_refusal():
    from app.services.profile_validation import validate_extracted_fields
    assert validate_extracted_fields({"age": 17})["age"] == 17

