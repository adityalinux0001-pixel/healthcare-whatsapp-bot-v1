from datetime import datetime, timezone, timedelta
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import User, Subscription, PaymentLink
from app.payments.razorpay_client import create_payment_link
from app.whatsapp.client import send_text_message
from app.utils.logging_config import logger
from app.utils.security import mask_identifier


DEV_BYPASS_PAYMENT_ID_PREFIX = "DEV_BYPASS_"


async def get_active_subscription(db: AsyncSession, user: User) -> Subscription | None:
    
    now = datetime.now(timezone.utc)
    sub = await db.scalar(
        select(Subscription).where(
            Subscription.user_id == user.id,
            Subscription.status == "active",
            Subscription.start_date <= now,
            Subscription.end_date > now,
        ).order_by(Subscription.end_date.desc())
    )
    if sub:
        return sub

    # DEV/TEST ONLY — see settings.bypass_subscription. Hard-guarded by
    # environment so this can never activate in production even if the flag
    # is left on in a shared .env by mistake.
    if settings.bypass_subscription and settings.environment != "production":
        return await _get_or_create_dev_bypass_subscription(db, user, now)

    return None


async def _get_or_create_dev_bypass_subscription(
    db: AsyncSession, user: User, now: datetime
) -> Subscription:
    """DEV/TEST ONLY. Creates/reuses one real Subscription row per user so the
    rest of the app (diet plan FK, renewal-reminder queries, historical plan
    lookups) behaves exactly as it would for a real paying subscriber — this
    is not a fake in-memory object, it's a normal DB row, just $0 and tagged
    so it's easy to find and clean up later.
    """
    payment_id = f"{DEV_BYPASS_PAYMENT_ID_PREFIX}{user.id}"
    existing = await db.scalar(
        select(Subscription).where(Subscription.razorpay_payment_id == payment_id)
    )
    if existing:
        if existing.end_date <= now:
            existing.end_date = now + timedelta(days=settings.subscription_days)
            existing.status = "active"
            await db.commit()
        return existing

    sub = Subscription(
        user_id=user.id,
        razorpay_payment_id=payment_id,
        amount_inr=0,
        start_date=now,
        end_date=now + timedelta(days=settings.subscription_days),
        status="active",
    )
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    logger.warning("dev_bypass_subscription_active", user_id=user.id, phone=mask_identifier(user.phone_number))
    return sub


async def prompt_payment(db: AsyncSession, user: User) -> None:
    """Send payment link — reuse an existing pending link if it hasn't expired.

    PaymentLink is the DB source of truth; Redis is only a fast-path cache.
    """
    from app.redis_client import redis_client

    phone = user.phone_number
    cache_key = f"pending_link:{phone}"

    cached_url = await redis_client.get(cache_key)
    if cached_url:
        await send_text_message(phone, _payment_message(cached_url))
        return

    now = datetime.now(timezone.utc)
    existing: PaymentLink | None = await db.scalar(
        select(PaymentLink).where(
            PaymentLink.user_id == user.id,
            PaymentLink.status == "pending",
            PaymentLink.expires_at > now,
        ).order_by(PaymentLink.created_at.desc())
    )
    if existing:
        await redis_client.set(cache_key, existing.short_url, ex=3600)
        await send_text_message(phone, _payment_message(existing.short_url))
        return

    short_url, link_id = await asyncio.to_thread(
        create_payment_link, phone, settings.subscription_price_inr
    )
    expires_at = now + timedelta(hours=24)

    db.add(PaymentLink(
        user_id=user.id,
        razorpay_link_id=link_id,
        short_url=short_url,
        amount_inr=settings.subscription_price_inr,
        status="pending",
        expires_at=expires_at,
    ))
    await db.commit()

    await redis_client.set(cache_key, short_url, ex=3600)
    await send_text_message(phone, _payment_message(short_url))


def _payment_message(url: str) -> str:
    return (
        f"Hello! I am your AI Health Assistant. 🌿\n\n"
        f"Subscribe to the {settings.subscription_days}-day plan to get started "
        f"— ₹{settings.subscription_price_inr} only:\n\n"
        f"{url}\n\n"
        f"Once payment is completed, we can get started right away!"
    )


async def mark_link_paid(
    db: AsyncSession,
    razorpay_link_id: str,
    razorpay_payment_id: str,
    phone_number: str,
) -> None:
    """Mark the DB payment link paid and clear its Redis fast-path cache."""
    from app.redis_client import redis_client

    link: PaymentLink | None = await db.scalar(
        select(PaymentLink).where(PaymentLink.razorpay_link_id == razorpay_link_id)
    )
    if link:
        link.status = "paid"
        link.paid_at = datetime.now(timezone.utc)
        link.razorpay_payment_id = razorpay_payment_id
        await db.commit()

    await redis_client.delete(f"pending_link:{phone_number}")


async def send_renewal_reminders(db: AsyncSession) -> None:
    """Run daily — nudge users whose current subscription expires within 2 days."""
    now = datetime.now(timezone.utc)
    soon = now + timedelta(days=2)
    result = await db.execute(
        select(Subscription).where(
            Subscription.status == "active",
            Subscription.start_date <= now,
            Subscription.end_date > now,
            Subscription.end_date <= soon,
        )
    )
    for sub in result.scalars().all():
        user = await db.get(User, sub.user_id)
        if user:
            try:
                await prompt_payment(db, user)
            except Exception:
                logger.exception("renewal_reminder_failed", user_id=sub.user_id)
