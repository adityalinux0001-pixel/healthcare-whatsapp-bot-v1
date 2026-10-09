"""Temporary launch controls for the first Hair & Scalp Assistant users."""


HAIR_CARE_LAUNCH_HOLD_NOTICE = (
    "✅ Payment received — thank you, and welcome! 🎉\n\n"
    "Don't worry, your money is safe with us. 💚\n\n"
    "We're setting up your personalized daily hair-care plan and will start sending it within the "
    "next 2–3 business days. You don't need to do anything — we'll message you right here as soon as "
    "your first routine is on its way. 🌿\n\n"
    "Your subscription period has not started yet. It begins the day your first daily plan is sent, "
    "so you won't lose a single day. If you already have a running subscription, this paid period "
    "stays queued until it is next in line.\n\n"
    "Our service does not diagnose conditions or prescribe treatment."
)


HAIR_CARE_LAUNCH_HOLD_STANDBY_REPLY = (
    "Thanks for your message! 🌿 Your plan is confirmed and your first routine will start reaching you "
    "within the next 2–3 business days. We'll message you right here as soon as it begins, and you'll "
    "be able to ask your hair & scalp questions here once your plan starts."
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