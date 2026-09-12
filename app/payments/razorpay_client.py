from datetime import datetime, timezone, timedelta

import razorpay

from app.config import settings

_client = razorpay.Client(auth=(settings.razorpay_key_id, settings.razorpay_key_secret))


def create_payment_link(phone_number: str, amount_inr: int) -> tuple[str, str]:
    """Returns (short_url, razorpay_link_id)."""
    expires_at = int((datetime.now(timezone.utc) + timedelta(hours=24)).timestamp())
    link = _client.payment_link.create({
        "amount": amount_inr * 100,
        "currency": "INR",
        "accept_partial": False,
        "expire_by": expires_at,
        "description": f"{settings.subscription_days}-day AI Diet Plan subscription",
        "customer": {"contact": phone_number},
        "notify": {"sms": True, "whatsapp": False},
        "reminder_enable": True,
        "notes": {"phone_number": phone_number},
        "callback_method": "get",
    })
    return link["short_url"], link["id"]
