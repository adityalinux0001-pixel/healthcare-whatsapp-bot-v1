import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]

def test_age_gate_uses_12_to_75_inclusive():
    src = (ROOT / "app/services/conversation_service.py").read_text()
    assert "not 12 <= user.age <= 75" in src


def test_old_adult_only_gate_is_removed():
    src = (ROOT / "app/services/conversation_service.py").read_text()
    assert "user.age < 18" not in src
    assert "aged 18 and over" not in src


def test_prompt_declares_supported_range():
    src = (ROOT / "app/llm/prompts.py").read_text()
    assert "supported age range is 12 to 75 inclusive" in src
