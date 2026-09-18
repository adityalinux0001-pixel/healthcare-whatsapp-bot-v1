"""ARQ worker for conversations, payments and daily plan delivery."""
from __future__ import annotations

from arq import cron
from arq.connections import RedisSettings
from zoneinfo import ZoneInfo

from app.config import settings
from app.database import AsyncSessionLocal
from app.utils.logging_config import logger, configure_logging
from app.utils.security import mask_identifier

IST = ZoneInfo("Asia/Kolkata")


async def process_incoming_message(ctx, phone: str, text: str, wa_message_id: str) -> None:
    from app.services.conversation_service import handle_incoming_message
    from app.whatsapp.client import show_typing_indicator

    await show_typing_indicator(wa_message_id)
    async with AsyncSessionLocal() as db:
        try:
            await handle_incoming_message(db, phone, text, wa_message_id)
        except Exception:
            logger.exception("process_incoming_message_failed", phone=mask_identifier(phone))
            raise


async def process_payment_success(ctx, phone_number: str, amount_inr: float, payment_id: str, link_id: str) -> None:
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select
    from app.models import User, Subscription, DietPlan
    from app.services.subscription_service import mark_link_paid
    from app.whatsapp.client import send_text_message

    async with AsyncSessionLocal() as db:
        user = await db.scalar(
            select(User).where(User.phone_number == phone_number).with_for_update()
        )
        if not user:
            user = User(phone_number=phone_number)
            db.add(user)
            await db.flush()

        now = datetime.now(timezone.utc)
        existing = await db.scalar(
            select(Subscription).where(Subscription.razorpay_payment_id == payment_id)
        )
        created = False
        if not existing:
            latest_end = await db.scalar(
                select(Subscription.end_date)
                .where(Subscription.user_id == user.id)
                .order_by(Subscription.end_date.desc())
                .limit(1)
            )
            start_date = max(now, latest_end) if latest_end else now
            db.add(Subscription(
                user_id=user.id,
                razorpay_payment_id=payment_id,
                amount_inr=amount_inr,
                start_date=start_date,
                end_date=start_date + timedelta(days=settings.subscription_days),
                status="active",
            ))
            await db.commit()
            created = True

        await mark_link_paid(db, link_id, payment_id, phone_number)

        if created and not user.onboarding_complete:
            # Backward-compatible handling for any payment that was initiated before
            # onboarding was completed: keep the existing onboarding continuation.
            from app.services.onboarding_extract import ONBOARDING_ORDER, QUESTIONS

            await send_text_message(
                phone_number,
                "✅ Payment received! Your plan is now active.\n\n"
                f"{QUESTIONS[ONBOARDING_ORDER[0]]}",
            )
        elif user.onboarding_complete:
            # First-time paid users need their first plan immediately. Existing users
            # receive the next plan from the normal 06:00 IST daily cron instead.
            existing_plan = await db.scalar(
                select(DietPlan.id)
                .where(DietPlan.user_id == user.id)
                .limit(1)
            )

            if existing_plan is None:
                from app.redis_client import get_arq_pool

                pool = await get_arq_pool()
                await pool.enqueue_job("generate_diet_plan_for_user", user.id, True)
                message = (
                    "✅ Payment received! Your plan is now active.\n\n"
                    "Your first personalized diet and exercise plan is being prepared. 🌿"
                )
            else:
                message = (
                    "✅ Payment received! Your plan is now active.\n\n"
                    "Your next personalized diet and exercise plan will be sent at 6:00 AM IST. 🌿"
                )

            await send_text_message(phone_number, message)
    logger.info("payment_success_processed", phone=mask_identifier(phone_number), payment_id=mask_identifier(payment_id))


async def generate_diet_plan_for_user(ctx, user_id: int, prefer_text: bool = False) -> None:
    from app.services.diet_plan_service import generate_plan_for_user
    try:
        await generate_plan_for_user(user_id, prefer_text=prefer_text)
    except Exception:
        logger.exception("generate_diet_plan_failed", user_id=user_id)
        raise


async def daily_diet_plan_job(ctx) -> None:
    from app.services.diet_plan_service import generate_and_send_daily_plans
    await generate_and_send_daily_plans()


async def subscription_reminder_job(ctx) -> None:
    from app.services.subscription_service import send_renewal_reminders
    async with AsyncSessionLocal() as db:
        await send_renewal_reminders(db)


async def worker_heartbeat_job(ctx) -> None:
    from app.redis_client import redis_client
    await redis_client.set(settings.worker_heartbeat_key, "ok", ex=120)


async def shutdown(ctx) -> None:
    from app.database import engine
    from app.redis_client import redis_client, close_arq_pool
    from app.whatsapp.client import close_http_client
    from app.llm.gemini_client import close_client as close_gemini_client

    await close_http_client()
    await close_gemini_client()
    await close_arq_pool()
    await redis_client.aclose()
    await engine.dispose()
    logger.info("worker_stopped")


async def startup(ctx) -> None:
    configure_logging(settings.environment)
    from app.redis_client import redis_client
    await redis_client.set(settings.worker_heartbeat_key, "ok", ex=120)
    logger.info("worker_started")


class WorkerSettings:
    functions = [
        process_incoming_message,
        process_payment_success,
        generate_diet_plan_for_user,
    ]
    cron_jobs = [
        cron(worker_heartbeat_job, minute=set(range(60))),
        cron(daily_diet_plan_job, hour=6, minute=0),
        cron(subscription_reminder_job, hour=10, minute=0),
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    timezone = IST
    max_jobs = 6
    job_timeout = 300
    max_tries = 3
    keep_result = 300