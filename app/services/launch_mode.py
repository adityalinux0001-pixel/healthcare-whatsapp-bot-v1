"""Temporary launch controls for the first Hair & Scalp Assistant users."""


HAIR_CARE_LAUNCH_HOLD_NOTICE = (
    "✅ Your subscription is active.\n\n"
    "Thank you for joining our Hair & Scalp Assistant. We’re preparing your personalized "
    "daily hair-care plan and expect to begin sending it within the next 2–3 business days.\n\n"
    "While we get everything ready, you can message us here with general hair and scalp-care questions.\n\n"
    "Please note: our assistant provides general information and does not diagnose conditions or prescribe treatment."
)


def routine_unavailable_notice(reason: str | None) -> str:
    """Avoid promising an automated routine when an eligibility/safety gate blocks it."""
    if reason == "high_risk_profile":
        return (
            "✅ Your subscription is active.\n\n"
            "You can ask general hair and scalp-care questions here. Based on health information "
            "previously saved to your profile, I can’t safely prepare an automated personalized "
            "routine. Please consult a qualified clinician for advice tailored to your health history."
        )
    if reason == "age_out_of_range":
        return (
            "✅ Your subscription is active.\n\n"
            "You can ask general hair and scalp-care questions here. Automated daily routines are "
            "currently available only for users aged 12–75. Please contact support to discuss your subscription."
        )
    return (
        "✅ Your subscription is active.\n\n"
        "You can ask general hair and scalp-care questions here. An automated daily routine isn’t "
        "available for this profile at the moment."
    )
