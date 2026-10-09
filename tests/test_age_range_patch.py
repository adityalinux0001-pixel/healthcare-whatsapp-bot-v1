from pathlib import Path
from app.models import User
from app.services.onboarding_extract import QUESTIONS

ROOT = Path(__file__).parents[1]


def test_hair_onboarding_does_not_apply_the_old_diet_plan_age_gate():
    src = (ROOT / "app/services/conversation_service.py").read_text()
    assert "not 12 <= user.age <= 75" not in src
    assert "personalized diet and exercise service is available" not in src


def test_minors_are_not_asked_the_optional_sexual_activity_question():
    user = User(phone_number="minor", age=16)
    assert "sexually_active" not in user.missing_fields()
    assert "currently sexually active" in QUESTIONS["sexually_active"]


def test_hair_onboarding_prompt_is_hair_focused():
    src = (ROOT / "app/llm/prompts.py").read_text()
    assert "Hair & Scalp Assistant" in src
    assert "Do not turn hair questions into weight-loss" in src
