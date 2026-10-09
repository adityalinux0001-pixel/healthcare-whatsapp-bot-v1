from app.knowledge.safety import detect_red_flag, detect_hair_concern


def test_emergency_red_flag_detection():
    assert detect_red_flag("I am having difficulty breathing") == "breathing emergency"
    assert detect_red_flag("what should I do for dandruff") is None


def test_emergency_red_flag_negation():
    assert detect_red_flag("I do not have chest pain") is None


def test_hair_concern_detection_routes_urgent_symptoms_to_clinician():
    assert detect_hair_concern("I have sudden hair loss") == "sudden_or_patchy_hair_loss"
    assert detect_hair_concern("there is pus on my scalp") == "scalp_inflammation_or_infection"
    assert detect_hair_concern("my eyebrows are falling out") == "eyebrow_or_body_hair_loss"


def test_hair_concern_negation_and_ordinary_question():
    assert detect_hair_concern("I do not have sudden hair loss") is None
    assert detect_hair_concern("how can I reduce normal hair breakage") is None
