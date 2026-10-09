"""ARQ worker for Hair & Scalp conversations and subscription payments."""
from __future__ import annotations

from arq import cron
from arq.worker import Retry
from arq.connections import RedisSettings
from zoneinfo import ZoneInfo

from app.config import settings
from app.database import AsyncSessionLocal
from app.utils.logging_config import logger, configure_logging
from app.utils.security import mask_identifier

IST = ZoneInfo("Asia/Kolkata")


async def process_incoming_message(
    ctx, phone: str, text: str, wa_message_id: str, button_id: str | None = None
) -> None:
    from app.services.conversation_service import handle_incoming_message
    from app.whatsapp.client import show_typing_indicator

    await show_typing_indicator(wa_message_id)
    async with AsyncSessionLocal() as db:
        try:
            await handle_incoming_message(db, phone, text, wa_message_id, button_id=button_id)
        except Exception:
            logger.exception("process_incoming_message_failed", phone=mask_identifier(phone))
            raise


async def process_payment_success(ctx, phone_number: str, amount_inr: float, payment_id: str, link_id: str) -> None:
    from datetime import datetime, timezone
    from sqlalchemy import select
    from app.models import User, Subscription
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
            # Every newly purchased term starts on its first successfully sent
            # routine, not the payment timestamp. If the user already has an
            # active period, this paid term remains queued as pending until it is
            # next in line; get_paid_subscription prefers active, then oldest pending.
            db.add(Subscription(
                user_id=user.id,
                razorpay_payment_id=payment_id,
                amount_inr=amount_inr,
                start_date=None,
                end_date=None,
                term_days=settings.subscription_days,
                status="pending",
            ))
            await db.commit()
            created = True

        await mark_link_paid(db, link_id, payment_id, phone_number)

        if user.hair_onboarding_complete:
            from app.services.hair_care_plan_service import routine_eligibility_block_reason
            from app.services.launch_mode import (
                HAIR_CARE_LAUNCH_HOLD_NOTICE, routine_unavailable_notice,
            )
            eligibility_reason = routine_eligibility_block_reason(
                age=user.age,
                medical_conditions=user.medical_conditions,
                hair_onboarding_complete=user.hair_onboarding_complete,
            )
            if settings.hair_care_launch_hold:
                message = (
                    HAIR_CARE_LAUNCH_HOLD_NOTICE
                    if eligibility_reason is None
                    else routine_unavailable_notice(eligibility_reason)
                )
                await send_text_message(phone_number, message)
                logger.info(
                    "payment_confirmed_launch_hold", user_id=user.id,
                    routine_eligible=eligibility_reason is None,
                )
            else:
                message = (
                    "✅ Payment received. Your paid plan period starts when the first daily hair-care routine under that period is successfully sent—not on the payment date. If you already have a running subscription, this paid period stays queued until it is next in line.\n\n"
                    + (
                        "Your first routine is being prepared. "
                        if eligibility_reason is None else
                        "We can't safely prepare an automated personalized routine for this profile right now. Your subscription period has not started; please contact our support team to discuss next steps. "
                        if eligibility_reason == "high_risk_profile" else
                        "Automated daily routines are currently available only for users aged 12–75. Your subscription period has not started; please contact our support team to discuss next steps. "
                    )
                    + "Our service provides general information and does not diagnose or prescribe treatment."
                )
                await send_text_message(phone_number, message)
                if eligibility_reason is None:
                    from app.redis_client import get_arq_pool
                    from datetime import datetime
                    pool = await get_arq_pool()
                    local_date = datetime.now(IST).date().isoformat()
                    await pool.enqueue_job(
                        "generate_hair_care_plan_for_user", user.id,
                        _job_id=f"hair_resume:{user.id}:{local_date}:payment",
                    )
        else:
            from app.services.onboarding_extract import ONBOARDING_ORDER, QUESTIONS
            from app.knowledge.safety import consent_request_message

            if settings.require_health_consent and user.health_data_consent_at is None:
                message = (
                    "✅ Payment received. Your paid plan period starts when the first daily hair-care routine under that period is successfully sent—not on the payment date. If you already have a running subscription, this paid period stays queued until it is next in line.\n\n"
                    + consent_request_message()
                )
            else:
                message = (
                    "✅ Payment received. Your paid plan period starts when the first daily hair-care routine under that period is successfully sent—not on the payment date. If you already have a running subscription, this paid period stays queued until it is next in line.\n\n"
                    f"{QUESTIONS[ONBOARDING_ORDER[0]]}"
                )
            await send_text_message(phone_number, message)
    logger.info("payment_success_processed", phone=mask_identifier(phone_number), payment_id=mask_identifier(payment_id))


async def generate_diet_plan_for_user(ctx, user_id: int, prefer_text: bool = False) -> None:
    # Keep this old queue handler registered during rolling deployments so any
    # delayed Redis job is acknowledged safely without creating a diet plan.
    logger.warning("legacy_diet_plan_job_skipped_hair_assistant", user_id=user_id)


async def daily_diet_plan_job(ctx) -> None:
    # Compatibility no-op if an old scheduler entry survives a deployment.
    logger.info("legacy_daily_diet_plan_scheduler_disabled")


async def generate_hair_care_plan_for_user(ctx, user_id: int) -> None:
    if settings.hair_care_launch_hold:
        logger.info("hair_care_launch_hold_plan_job_skipped", user_id=user_id)
        return
    from app.services.hair_care_plan_service import generate_plan_for_user
    from app.llm.gemini_client import PlanGenerationValidationError
    from app.knowledge.store import KnowledgeBaseNotReady

    job_try = ctx.get("job_try", 1)
    max_tries = WorkerSettings.max_tries
    try:
        await generate_plan_for_user(user_id)
    except (PlanGenerationValidationError, KnowledgeBaseNotReady) as exc:
        logger.error(
            "generate_hair_care_plan_retryable_failure",
            user_id=user_id, job_try=job_try, error_type=type(exc).__name__,
        )
        if job_try < max_tries:
            raise Retry(defer=60 * job_try)
        raise
    except Exception as exc:
        logger.exception(
            "generate_hair_care_plan_failed",
            user_id=user_id, job_try=job_try, error_type=type(exc).__name__,
        )
        if job_try < max_tries:
            raise Retry(defer=30 * job_try)
        raise


async def daily_hair_care_plan_job(ctx) -> None:
    if settings.hair_care_launch_hold:
        logger.info("hair_care_launch_hold_daily_scheduler_skipped")
        return
    from app.services.hair_care_plan_service import generate_and_send_daily_plans
    await generate_and_send_daily_plans()


async def retry_hair_care_checkins_job(ctx) -> None:
    if settings.hair_care_launch_hold:
        return
    from app.services.hair_care_plan_service import retry_pending_checkin_prompts
    await retry_pending_checkin_prompts()


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
        generate_diet_plan_for_user,  # compatibility for delayed legacy ARQ jobs; service is a safe no-op
        daily_diet_plan_job,           # compatibility for a legacy cron job already queued in Redis
        generate_hair_care_plan_for_user,
    ]
    cron_jobs = [
        cron(worker_heartbeat_job, minute=set(range(60))),
        # Daily hair-care routine: service checks subscription, adult age, local day,
        # idempotency and check-in gate. Generation occurs at 06:00; delivery waits
        # for an open WhatsApp customer-service window if needed.
        cron(daily_hair_care_plan_job, hour=6, minute=0),
        # Retry definite check-in prompt failures, never ambiguous provider outcomes.
        cron(retry_hair_care_checkins_job, minute=set(range(0, 60, 15))),
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