
from __future__ import annotations

import re

_RED_FLAGS = {
    "chest pain": [
        "chest pain",
        "chest pressure",
        "pain in my chest",
    ],
    "breathing emergency": [
        "difficulty breathing", "can't breathe", "cannot breathe", "shortness of breath",
        "trouble breathing", "breathing difficulty",
    ],
    "stroke-like emergency": [
        "face drooping", "one side weakness", "slurred speech",
        "facial droop", "sudden weakness on one side",
    ],
    "severe bleeding": [
        "severe bleeding", "bleeding won't stop", "uncontrolled bleeding",
    ],
    "loss of consciousness": [
        "unconscious", "passed out", "lost consciousness",
    ],
    "severe allergic reaction": [
        "anaphylaxis", "swelling of tongue", "throat swelling", "tongue swelling",
        "difficulty swallowing with swelling",
    ],
}

_NEGATION_PREFIXES = (
    "no ", "not ", "don't have ", "dont have ", "i don't have ", "i do not have ",
    "i am not having ", "not having ",
)
_NEGATION_SUFFIXES = (
    " not", " is not", " are not",
)

_HIGH_RISK_PATTERNS = (
    "pregnant", "pregnancy", "pregnancy", "dialysis", "kidney failure", "renal failure",
    "eating disorder", "anorexia", "bulimia", "heart failure", "recent surgery",
    "post surgery", "organ transplant",
)


def _is_negated(normalized: str, phrase: str) -> bool:
    index = normalized.find(phrase)
    while index >= 0:
        prefix = normalized[max(0, index - 32):index]
        suffix = normalized[index + len(phrase): index + len(phrase) + 24]
        if any(prefix.endswith(p) for p in _NEGATION_PREFIXES) or any(
            suffix.startswith(p) for p in _NEGATION_SUFFIXES
        ):
            index = normalized.find(phrase, index + 1)
            continue
        return False
    return True


def detect_red_flag(text: str) -> str | None:
    normalized = re.sub(r"\s+", " ", text.casefold()).strip()
    for reason, phrases in _RED_FLAGS.items():
        for phrase in phrases:
            if phrase in normalized and not _is_negated(normalized, phrase):
                return reason
    return None


def detect_high_risk_profile(profile: dict) -> str | None:
    text = str(profile.get("medical_conditions") or "").casefold()
    for pattern in _HIGH_RISK_PATTERNS:
        if pattern in text:
            return pattern
    return None


def emergency_response() -> str:
    return (
        "⚠️ The information you provided may describe an urgent symptom. "
        "I cannot safely diagnose or manage this as a routine diet/exercise question. "
        "Please seek urgent medical evaluation, and contact local emergency services if symptoms are severe or worsening."
    )


def consent_request_message() -> str:
    return (
        "Hello! 🌿 I will process your age, weight, allergies, and health-related information "
        "to provide personalized diet and exercise guidance. This is sensitive information.\n\n"
        "I need your consent to use and store this information for your plans and conversations. "
        "Reply *YES* to continue. Reply *NO* if you do not want to provide consent."
    )


def consent_declined_message() -> str:
    return (
        "Understood. I will not collect health-related profile information. 🌿\n"
        "When you are ready, reply *YES* to start personalized onboarding."
    )
