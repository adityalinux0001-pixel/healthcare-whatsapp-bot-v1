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
    "pregnant", "pregnancy", "dialysis", "kidney failure", "renal failure",
    "eating disorder", "anorexia", "bulimia", "heart failure", "recent surgery",
    "post surgery", "organ transplant",
    "cancer", "chemotherapy", "chemo", "oncology", "tumor", "tumour",
    "radiation therapy", "leukemia", "leukaemia", "lymphoma",
)


_HAIR_CONCERN_PATTERNS = {
    "sudden_or_patchy_hair_loss": (
        "sudden hair loss", "hair falling out in clumps", "hair comes out in clumps",
        "sudden bald patch", "bald patch appeared", "patchy hair loss", "rapid hair loss",
        "suddenly losing hair", "suddenly lost hair",
    ),
    "scalp_inflammation_or_infection": (
        "painful scalp", "scalp pain", "burning scalp", "tender scalp", "swollen scalp",
        "scalp swelling", "pus on my scalp", "scalp pus", "sores on my scalp",
        "scalp sores", "bleeding scalp", "red inflamed scalp",
    ),
    "eyebrow_or_body_hair_loss": (
        "eyebrow hair loss", "losing my eyebrows", "my eyebrows are falling out",
        "eyebrows falling out", "eyelashes falling out", "my eyelashes are falling out",
        "body hair falling out", "lost body hair",
    ),
}


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


def detect_hair_concern(text: str) -> str | None:
    """Detect hair/scalp symptoms that should receive clinician-referral guidance."""
    normalized = re.sub(r"\s+", " ", text.casefold()).strip()
    for reason, phrases in _HAIR_CONCERN_PATTERNS.items():
        for phrase in phrases:
            if phrase in normalized and not _is_negated(normalized, phrase):
                return reason
    return None


def hair_concern_response(reason: str | None = None) -> str:
    if reason == "sudden_or_patchy_hair_loss":
        detail = "Sudden, rapid, or patchy hair loss needs prompt assessment by a dermatologist."
    elif reason == "scalp_inflammation_or_infection":
        detail = "Scalp pain, burning, swelling, sores, or pus should be assessed promptly by a healthcare professional."
    elif reason == "eyebrow_or_body_hair_loss":
        detail = "Hair loss affecting eyebrows, eyelashes, or other body areas should be assessed by a dermatologist."
    else:
        detail = "The symptom you described may need professional assessment."
    return (
        detail + " I can't diagnose the cause in chat. Please arrange a medical assessment soon, "
        "especially if it is worsening or the scalp is painful or inflamed."
    )


def detect_high_risk_profile(profile: dict) -> str | None:
    normalized = re.sub(r"\s+", " ", str(profile.get("medical_conditions") or "").casefold()).strip()
    for pattern in _HIGH_RISK_PATTERNS:
        if pattern in normalized and not _is_negated(normalized, pattern):
            return pattern
    return None


def emergency_response() -> str:
    return (
        "⚠️ The symptom you described may be urgent. I can't diagnose or manage an emergency in chat. "
        "Please seek urgent medical care now, and contact local emergency services if symptoms are severe or worsening."
    )


def high_risk_profile_message() -> str:
    return (
        "Thank you for sharing that. 🌿 Based on the health information in your profile, "
        "personalized medical treatment advice isn't something I can safely provide in chat. "
        "Please consult a qualified healthcare professional who knows your medical history. "
        "I can still share general, evidence-based information about hair and scalp health."
    )


def consent_request_message() -> str:
    return (
        "Hello! 🌿 I'm your Hair & Scalp Assistant. To personalize hair-related guidance, I may store your age, city, "
        "hair-washing habits, water type, height/weight, and food-habit answers. For adults, one question is about "
        "sexual activity; you may choose *Prefer not to say*.\n\n"
        "This includes your family hair-loss history. This information can be sensitive. Please reply *YES* if you agree to its storage and use for this service, "
        "or *NO* if you do not agree. You can stop onboarding at any time."
    )


def consent_declined_message() -> str:
    return (
        "Understood. I won't collect your hair-profile answers without your consent. 🌿\n"
        "If you change your mind, reply *YES* to start onboarding."
    )