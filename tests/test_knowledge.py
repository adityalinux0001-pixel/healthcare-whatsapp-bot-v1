from app.knowledge.safety import detect_red_flag


def test_red_flag_detection():
    assert detect_red_flag("I am having difficulty breathing") == "breathing emergency"
    assert detect_red_flag("what should I eat for breakfast") is None


def test_red_flag_negation():
    from app.knowledge.safety import detect_red_flag
    assert detect_red_flag("I do not have chest pain") is None

