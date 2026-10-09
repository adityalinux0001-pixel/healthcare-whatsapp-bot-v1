"""Temporary launch controls for the first Hair & Scalp Assistant users."""


HAIR_CARE_LAUNCH_HOLD_NOTICE = (
    "✅ Payment received. Your subscription period has not started yet.\n\n"
    "Your paid period begins when the first daily hair-care plan under that period is successfully sent. "
    "If you already have a running subscription, this paid period stays queued until it is next in line.\n\n"
    "Thank you for joining our Hair & Scalp Assistant. We’re preparing your personalized "
    "daily hair-care plan and expect to begin sending it within the next 2–3 business days.\n\n"
    "During this preparation period, hair/scalp question-answering and personalized guidance are not available yet. "
    "We’ll notify you when the service is ready.\n\n"
    "Our service does not diagnose conditions or prescribe treatment."
)


HAIR_CARE_LAUNCH_HOLD_STANDBY_REPLY = (
    "Thanks for your message. Our Hair & Scalp Assistant is still being prepared, so "
    "hair/scalp questions and personalized guidance are not available yet. We expect to begin "
    "sending daily hair-care plans within the next 2–3 business days. We’ll notify you when "
    "the service is ready."
)


def routine_unavailable_notice(reason: str | None) -> str:
    """Explain why an automated routine is unavailable without opening Q&A during launch hold."""
    if reason == "high_risk_profile":
        return (
            "✅ Payment received. Your subscription period has not started yet.\n\n"
            "Based on health information previously saved to your profile, we can’t safely prepare "
            "an automated personalized hair-care routine. Please consult a qualified clinician for "
            "advice tailored to your health history.\n\n"
            "The Hair & Scalp Assistant is still being prepared, so chat-based hair/scalp guidance "
            "is not available yet. We’ll update you when the service is ready."
        )
    if reason == "age_out_of_range":
        return (
            "✅ Payment received. Your subscription period has not started yet.\n\n"
            "Automated daily routines are currently available only for users aged 12–75. Please "
            "contact support to discuss your subscription.\n\n"
            "The Hair & Scalp Assistant is still being prepared, so chat-based hair/scalp guidance "
            "is not available yet. We’ll update you when the service is ready."
        )
    return (
        "✅ Payment received. Your subscription period has not started yet.\n\n"
        "An automated daily routine isn’t available for this profile at the moment. The Hair & "
        "Scalp Assistant is still being prepared, so chat-based hair/scalp guidance is not "
        "available yet. We’ll update you when the service is ready."
    )
