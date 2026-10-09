"""Daily hair-care routine lifecycle: generate, validate, deliver, and check in.

Hair routines are stored separately from legacy diet plans. Provider side effects are
claimed transactionally; ambiguous delivery results are never blindly resent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, time, timedelta
from zoneinfo import ZoneInfo
import re

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import RetryError

from app.database import AsyncSessionLocal
from app.config import settings
from app.models import User, HairCarePlan, Message
from app.knowledge.safety import detect_high_risk_profile
from app.llm.gemini_client import generate_hair_care_plan
from app.services.plan_revision_policy import is_duplicate_modification
from app.services.subscription_service import get_active_subscription
from app.whatsapp.client import send_text_message, send_reply_buttons
from app.utils.logging_config import logger

IST = ZoneInfo("Asia/Kolkata")
SERVICE_WINDOW_SECONDS = 24 * 60 * 60
PLAN_SEND_HOUR_IST = 6
# Preserve the product's existing supported routine age range; sexual-activity
# onboarding remains adults-only and is independent of routine eligibility.
MIN_ROUTINE_AGE = 12
MAX_ROUTINE_AGE = 75
CHECKIN_PENDING = "pending"
CHECKIN_BUTTON_PREFIX = "hck"
CHECKIN_CHOICES = {"done": "done", "not_done": "not_done", "skip": "skipped"}
# Close an unanswered check-in at the next local day boundary, without implying
# that the user actually replied.
CHECKIN_NO_RESPONSE = "no_response"
CHECKIN_ANSWERED = frozenset({*CHECKIN_CHOICES.values(), CHECKIN_NO_RESPONSE})
CHECKIN_BUTTON_TITLES = {"done": "✅ Done", "not_done": "❌ Not done", "skip": "⏭️ Skip"}
_GENERATION_STALE_AFTER = timedelta(minutes=15)

_CHECKIN_TEXT_ANSWERS = {
    "done": "done", "done!": "done", "ho gaya": "done", "hogaya": "done", "ho gya": "done",
    "hogya": "done", "done ho gaya": "done", "completed": "done",
    "not done": "not_done", "nahi hua": "not_done", "nhi hua": "not_done",
    "nahi kiya": "not_done", "nhi kiya": "not_done", "not done yet": "not_done",
    "skip": "skip", "skipped": "skip", "skip it": "skip", "skip kar do": "skip",
}


@dataclass
class PlanRevisionResult:
    plan: HairCarePlan | None
    is_duplicate: bool = False


def _plan_day_utc() -> datetime:
    local_date = datetime.now(IST).date()
    return datetime.combine(local_date, time.min, tzinfo=IST).astimezone(timezone.utc)


def _canonical_day_from_local_date(local_date) -> datetime:
    return datetime.combine(local_date, time.min, tzinfo=IST).astimezone(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _service_window_open(last_inbound_at: datetime | None) -> bool:
    last_in = _as_utc(last_inbound_at)
    return bool(last_in and (datetime.now(timezone.utc) - last_in).total_seconds() < SERVICE_WINDOW_SECONDS)


def is_routine_eligible_age(age: int | None) -> bool:
    """The existing daily-plan product supports ages 12–75 inclusive."""
    return age is not None and MIN_ROUTINE_AGE <= age <= MAX_ROUTINE_AGE


def routine_eligibility_block_reason(
    *, age: int | None, medical_conditions: str | None = None,
    hair_onboarding_complete: bool = True,
) -> str | None:
    """Return a non-sensitive reason code when an automated routine must be withheld.

    The new onboarding does not ask for medical conditions, but existing production
    profiles may already contain them. Keep the former high-risk safety boundary for
    those records while allowing general hair/scalp Q&A.
    """
    if not hair_onboarding_complete:
        return "hair_onboarding_incomplete"
    if not is_routine_eligible_age(age):
        return "age_out_of_range"
    if detect_high_risk_profile({"medical_conditions": medical_conditions}):
        return "high_risk_profile"
    return None


def is_routine_eligible_user(user: User) -> bool:
    return routine_eligibility_block_reason(
        age=user.age,
        medical_conditions=user.medical_conditions,
        hair_onboarding_complete=user.hair_onboarding_complete,
    ) is None


def checkin_button_id(plan_id: int, choice: str) -> str:
    return f"{CHECKIN_BUTTON_PREFIX}:{plan_id}:{choice}"


def parse_checkin_button_id(button_id: str | None) -> tuple[int, str] | None:
    parts = (button_id or "").split(":")
    if len(parts) != 3 or parts[0] != CHECKIN_BUTTON_PREFIX:
        return None
    if not parts[1].isdigit() or parts[2] not in CHECKIN_CHOICES:
        return None
    return int(parts[1]), parts[2]


def parse_checkin_text(text: str) -> str | None:
    normalized = " ".join(text.casefold().replace("✅", " ").replace("❌", " ").replace("⏭️", " ").split())
    return _CHECKIN_TEXT_ANSWERS.get(normalized)


async def _latest_plan(db, user_id: int) -> HairCarePlan | None:
    return await db.scalar(
        select(HairCarePlan).where(HairCarePlan.user_id == user_id)
        .order_by(HairCarePlan.plan_date.desc(), HairCarePlan.id.desc()).limit(1)
    )


async def get_pending_checkin_plan(db, user_id: int) -> HairCarePlan | None:
    plan = await _latest_plan(db, user_id)
    if plan and plan.delivery_status == "sent" and plan.checkin_status == CHECKIN_PENDING:
        await _expire_prior_day_checkin(db, plan)
        if plan.checkin_status != CHECKIN_PENDING:
            return None
        return plan
    return None


async def get_unanswered_prior_day_checkin(db, user_id: int) -> HairCarePlan | None:
    plan = await get_pending_checkin_plan(db, user_id)
    if plan and plan.plan_date < _plan_day_utc():
        return plan
    return None


async def _expire_prior_day_checkin(db: AsyncSession, plan: HairCarePlan | None) -> bool:
    """Close a check-in after a full 24-hour response window, without fabricating a reply."""
    if not plan or plan.delivery_status != "sent" or plan.checkin_status != CHECKIN_PENDING:
        return False
    sent_at = _as_utc(plan.sent_at)
    if not sent_at or datetime.now(timezone.utc) < sent_at + timedelta(seconds=SERVICE_WINDOW_SECONDS):
        return False
    plan.checkin_status = CHECKIN_NO_RESPONSE
    # Keep checkin_responded_at empty: no response was received.
    await db.commit()
    logger.info("hair_care_checkin_auto_closed_no_response", user_id=plan.user_id, plan_id=plan.id)
    return True


async def needs_plan_resume(db: AsyncSession, user: User) -> bool:
    if settings.hair_care_launch_hold or not is_routine_eligible_user(user):
        return False
    latest = await _latest_plan(db, user.id)
    await _expire_prior_day_checkin(db, latest)
    if latest and latest.delivery_status == "awaiting_window" and latest.content:
        return True
    # Ambiguous provider deliveries must be reconciled by an operator/provider
    # status event; never enqueue another routine that might duplicate a message.
    if latest and latest.delivery_status in {"unknown", "sending"}:
        return False
    today = _plan_day_utc()
    current = await db.scalar(select(HairCarePlan).where(
        HairCarePlan.user_id == user.id, HairCarePlan.plan_date == today
    ))
    if current:
        if current.delivery_status in {"sent", "sending", "unknown"}:
            return False
        if current.content and current.delivery_status in {"pending", "failed", "awaiting_window"}:
            return True
        if current.delivery_status == "generating":
            created = _as_utc(current.created_at)
            return bool(created and datetime.now(timezone.utc) - created > _GENERATION_STALE_AFTER)
        return current.delivery_status == "failed"
    if latest and latest.delivery_status == "sent" and latest.checkin_status == CHECKIN_PENDING:
        return False
    return datetime.now(IST).hour >= PLAN_SEND_HOUR_IST


async def send_checkin_prompt(phone: str, plan_id: int, day_number: int, *, reminder: bool = False) -> None:
    if reminder:
        body = (f"👋 Quick check-in first!\n\nDid you try your *Day {day_number}* hair-care routine?\n\n"
                "Tap one option below — your next routine follows after your reply. 🌿")
    else:
        body = (f"✅ *Day {day_number} hair-care check-in*\n\nDid you try today's hair-care routine?\n\n"
                "Tap one option below so I can keep your routine on track. 🌿")
    await send_reply_buttons(
        phone, body,
        [(checkin_button_id(plan_id, choice), CHECKIN_BUTTON_TITLES[choice]) for choice in CHECKIN_CHOICES],
    )


async def _start_checkin(user_id: int, plan_id: int, day_number: int) -> None:
    """Claim and send a check-in once; failed definite sends may be retried by cron."""
    async with AsyncSessionLocal() as db:
        plan = await db.scalar(
            select(HairCarePlan).where(HairCarePlan.id == plan_id).with_for_update()
        )
        user = await db.get(User, user_id)
        if not plan or not user or plan.checkin_status in CHECKIN_ANSWERED:
            return
        if plan.checkin_prompt_status in {"sent", "sending", "unknown"}:
            return
        if plan.checkin_prompt_attempts >= 3:
            plan.checkin_prompt_status = "failed"
            plan.checkin_prompt_last_error = "Maximum check-in prompt attempts reached."
            await db.commit()
            return
        last_inbound_at = await _last_inbound_at(db, user.id)
        if not _service_window_open(last_inbound_at):
            # WhatsApp free-form messages are not allowed outside the 24-hour window.
            return
        plan.checkin_status = CHECKIN_PENDING
        plan.checkin_prompt_status = "sending"
        plan.checkin_prompt_attempts += 1
        plan.checkin_sent_at = datetime.now(timezone.utc)
        plan.checkin_prompt_last_error = None
        phone = user.phone_number
        await db.commit()

    try:
        await send_checkin_prompt(phone, plan_id, day_number)
    except RetryError as exc:
        root = exc.last_attempt.exception()
        ambiguous = isinstance(root, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)) or (
            isinstance(root, httpx.HTTPStatusError) and root.response.status_code >= 500
        )
        await _mark_checkin_prompt_status(
            plan_id,
            "unknown" if ambiguous else "failed",
            error=("Ambiguous check-in delivery result; do not resend without provider reconciliation."
                   if ambiguous else str(root)),
        )
        logger.exception("hair_care_checkin_prompt_failed", user_id=user_id, plan_id=plan_id)
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
        await _mark_checkin_prompt_status(
            plan_id, "unknown", error="Ambiguous check-in delivery result; provider reconciliation required."
        )
        logger.exception("hair_care_checkin_prompt_delivery_unknown", user_id=user_id, plan_id=plan_id)
    except Exception as exc:
        ambiguous = isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code >= 500
        await _mark_checkin_prompt_status(
            plan_id, "unknown" if ambiguous else "failed", error=str(exc)
        )
        logger.exception("hair_care_checkin_prompt_failed", user_id=user_id, plan_id=plan_id)
    else:
        await _mark_checkin_prompt_status(plan_id, "sent")


async def _mark_checkin_prompt_status(plan_id: int, status: str, *, error: str | None = None) -> None:
    async with AsyncSessionLocal() as db:
        plan = await db.get(HairCarePlan, plan_id)
        if not plan:
            return
        plan.checkin_prompt_status = status
        plan.checkin_prompt_last_error = error[:2000] if error else None
        await db.commit()


async def retry_pending_checkin_prompts() -> None:
    """Retry definite check-in send failures, only inside an open WhatsApp window."""
    if settings.hair_care_launch_hold:
        logger.info("hair_care_launch_hold_checkin_retry_skipped")
        return
    now = datetime.now(timezone.utc)
    cutoff = now - _GENERATION_STALE_AFTER
    async with AsyncSessionLocal() as db:
        stale_sending = await db.scalars(
            select(HairCarePlan).where(
                HairCarePlan.delivery_status == "sent",
                HairCarePlan.checkin_status == CHECKIN_PENDING,
                HairCarePlan.checkin_prompt_status == "sending",
                HairCarePlan.checkin_sent_at < cutoff,
            ).with_for_update(skip_locked=True).limit(100)
        )
        for plan in stale_sending:
            # A worker may have crashed after Meta accepted the request; never blindly resend.
            plan.checkin_prompt_status = "unknown"
            plan.checkin_prompt_last_error = "Stale sending state; provider delivery outcome is ambiguous."
        last_inbound = (
            select(Message.user_id.label("user_id"), func.max(Message.created_at).label("last_inbound_at"))
            .where(Message.role == "user")
            .group_by(Message.user_id)
            .subquery()
        )
        retry_rows = await db.execute(
            select(HairCarePlan.id, HairCarePlan.user_id, HairCarePlan.day_number)
            .join(User, User.id == HairCarePlan.user_id)
            .join(last_inbound, last_inbound.c.user_id == User.id)
            .where(
                HairCarePlan.delivery_status == "sent",
                HairCarePlan.checkin_status == CHECKIN_PENDING,
                HairCarePlan.checkin_prompt_status.in_(["not_sent", "failed"]),
                HairCarePlan.checkin_prompt_attempts < 3,
                last_inbound.c.last_inbound_at >= now - timedelta(seconds=SERVICE_WINDOW_SECONDS),
            ).order_by(HairCarePlan.checkin_sent_at.asc().nullsfirst()).limit(100)
        )
        retry_items = list(retry_rows.all())
        await db.commit()

    for plan_id, user_id, day_number in retry_items:
        try:
            await _start_checkin(user_id, plan_id, day_number)
        except Exception:
            logger.exception("hair_care_checkin_retry_failed", user_id=user_id, plan_id=plan_id)


async def record_checkin(db: AsyncSession, user: User, plan_id: int, choice: str) -> tuple[str, HairCarePlan | None]:
    if choice not in CHECKIN_CHOICES:
        await db.commit()
        return "not_found", None
    plan = await db.scalar(select(HairCarePlan).where(
        HairCarePlan.id == plan_id, HairCarePlan.user_id == user.id
    ).with_for_update())
    if not plan:
        await db.commit()
        return "not_found", None
    if plan.checkin_status in CHECKIN_ANSWERED:
        await db.commit()
        return "already", plan
    if plan.delivery_status != "sent":
        await db.commit()
        return "not_found", None
    plan.checkin_status = CHECKIN_CHOICES[choice]
    plan.checkin_responded_at = datetime.now(timezone.utc)
    await db.commit()
    return "recorded", plan


async def _last_inbound_at(db, user_id: int) -> datetime | None:
    return await db.scalar(select(Message.created_at).where(
        Message.user_id == user_id, Message.role == "user"
    ).order_by(Message.created_at.desc()).limit(1))


async def _recent_plans(db, user_id: int, *, exclude_id: int | None = None) -> list[str]:
    stmt = select(HairCarePlan.content).where(
        HairCarePlan.user_id == user_id, HairCarePlan.content != ""
    )
    if exclude_id is not None:
        stmt = stmt.where(HairCarePlan.id != exclude_id)
    result = await db.execute(stmt.order_by(HairCarePlan.plan_date.desc()).limit(3))
    return list(result.scalars().all())


async def _mark_status(plan_id: int, status: str, *, error: str | None = None) -> None:
    async with AsyncSessionLocal() as db:
        plan = await db.get(HairCarePlan, plan_id)
        if plan:
            plan.delivery_status = status
            plan.last_send_error = error[:2000] if error else None
            await db.commit()


async def _send_plan(user_id: int, plan_id: int) -> None:
    # Defense in depth: even a stale/manual caller cannot deliver during launch hold.
    if settings.hair_care_launch_hold:
        logger.info("hair_care_launch_hold_direct_delivery_skipped", user_id=user_id, plan_id=plan_id)
        return
    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        plan = await db.scalar(select(HairCarePlan).where(HairCarePlan.id == plan_id).with_for_update())
        if not user or not plan or not plan.content:
            return
        if plan.delivery_status in {"sent", "sending", "unknown", "generating"}:
            return
        if not is_routine_eligible_user(user):
            plan.delivery_status = "failed"
            plan.last_send_error = (
                "Safety gate: automated routine withheld because the saved profile is not eligible; "
                "clinician review may be appropriate."
            )
            await db.commit()
            logger.warning("hair_care_plan_withheld_by_safety_gate", user_id=user_id, plan_id=plan_id)
            return
        if plan.send_attempts >= 3:
            plan.delivery_status = "failed"
            plan.last_send_error = plan.last_send_error or "Maximum delivery attempts reached; operator review required."
            await db.commit()
            logger.error("hair_care_plan_delivery_attempts_exhausted", user_id=user_id, plan_id=plan_id)
            return
        last_inbound_at = await _last_inbound_at(db, user.id)
        if not _service_window_open(last_inbound_at):
            plan.delivery_status = "awaiting_window"
            plan.last_send_error = "WhatsApp 24-hour customer-service window closed; delivery waits for a new inbound message."
            await db.commit()
            logger.info("hair_care_plan_delivery_deferred_window_closed", user_id=user.id, day_number=plan.day_number)
            return
        plan.delivery_status = "sending"
        plan.send_attempts += 1
        plan.last_send_attempt_at = datetime.now(timezone.utc)
        plan.last_send_error = None
        content = plan.content
        day_number = plan.day_number
        await db.commit()
        phone = user.phone_number

    try:
        response = await send_text_message(phone, content)
    except RetryError as exc:
        root = exc.last_attempt.exception()
        ambiguous = isinstance(root, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)) or (
            isinstance(root, httpx.HTTPStatusError) and root.response.status_code >= 500
        )
        await _mark_status(
            plan_id, "unknown" if ambiguous else "failed",
            error="Ambiguous WhatsApp delivery result; do not resend without provider reconciliation."
            if ambiguous else str(root),
        )
        if ambiguous:
            logger.error("hair_care_plan_delivery_unknown", user_id=user_id, day_number=day_number)
            return
        raise
    except httpx.HTTPStatusError as exc:
        ambiguous = exc.response.status_code >= 500
        await _mark_status(
            plan_id, "unknown" if ambiguous else "failed",
            error=f"WhatsApp HTTP {exc.response.status_code}",
        )
        if ambiguous:
            logger.error("hair_care_plan_delivery_unknown", user_id=user_id, day_number=day_number)
            return
        raise
    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
        await _mark_status(plan_id, "unknown", error="Ambiguous WhatsApp delivery result; provider reconciliation required.")
        logger.error("hair_care_plan_delivery_unknown", user_id=user_id, day_number=day_number)
        return
    except Exception as exc:
        await _mark_status(plan_id, "failed", error=str(exc))
        raise

    provider_message_id = None
    try:
        provider_message_id = response.get("messages", [{}])[0].get("id")
    except (AttributeError, IndexError, TypeError):
        pass

    async with AsyncSessionLocal() as db:
        plan = await db.get(HairCarePlan, plan_id)
        if not plan:
            return
        plan.delivery_status = "sent"
        plan.sent_at = datetime.now(timezone.utc)
        plan.provider_message_id = provider_message_id
        plan.last_send_error = None
        # Persist the check-in gate before sending interactive buttons. If the
        # worker crashes between these operations, the retry cron can recover it.
        if plan.checkin_status not in CHECKIN_ANSWERED:
            plan.checkin_status = CHECKIN_PENDING
        await db.commit()
    await _start_checkin(user_id, plan_id, day_number)


def _hair_plan_profile(user: User) -> dict:
    # Only include hair-routine-relevant fields. In particular, never pass the sensitive
    # sexual-activity answer, legacy weight-loss fields, or irrelevant medical history.
    source = user.profile_dict()
    return {
        key: source[key] for key in (
            "age", "hair_wash_frequency", "water_hardness", "family_hair_loss"
        ) if source.get(key) not in (None, "")
    }


async def generate_plan_for_user(user_id: int, *, prefer_text: bool = False) -> None:
    """Generate at most one queued/sent routine per user at a time.

    The cron creates/stores a routine at 06:00 IST even if the WhatsApp 24-hour
    window is closed. `_send_plan` then safely parks it in `awaiting_window` until
    the user next messages. This avoids unapproved free-form outbound messages.
    """
    if settings.hair_care_launch_hold:
        logger.info("hair_care_launch_hold_direct_generation_skipped", user_id=user_id)
        return

    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)
        if not user:
            return
        eligibility_reason = routine_eligibility_block_reason(
            age=user.age,
            medical_conditions=user.medical_conditions,
            hair_onboarding_complete=user.hair_onboarding_complete,
        )
        if eligibility_reason:
            # Mark any undelivered routine clearly for admin review instead of leaving
            # it in an apparently sendable state after eligibility changed.
            if eligibility_reason in {"age_out_of_range", "high_risk_profile"}:
                latest = await _latest_plan(db, user.id)
                if latest and latest.delivery_status in {"pending", "awaiting_window", "failed", "generating"}:
                    latest.delivery_status = "failed"
                    latest.last_send_error = (
                        "Safety/eligibility gate: routine withheld. General Q&A remains available; "
                        "review profile and clinical suitability before any manual action."
                    )
                    await db.commit()
            return
        subscription = await get_active_subscription(db, user)
        if not subscription:
            return

        # Serialize cron-triggered and message-triggered work for this user.
        await db.execute(select(User.id).where(User.id == user.id).with_for_update())
        today = _plan_day_utc()
        now = datetime.now(timezone.utc)
        latest = await _latest_plan(db, user.id)
        await _expire_prior_day_checkin(db, latest)

        if latest and latest.delivery_status == "sending":
            sending_at = _as_utc(latest.last_send_attempt_at)
            if sending_at and now - sending_at > _GENERATION_STALE_AFTER:
                latest.delivery_status = "unknown"
                latest.last_send_error = "Stale sending state; delivery outcome is ambiguous and requires provider reconciliation."
                await db.commit()
                logger.error("hair_care_plan_stale_send_marked_unknown", user_id=user.id, plan_id=latest.id)
            else:
                await db.commit()
            return
        if latest and latest.delivery_status == "unknown":
            logger.error("hair_care_plan_unknown_delivery_blocks_new_plan", user_id=user.id, plan_id=latest.id)
            await db.commit()
            return
        if latest and latest.delivery_status == "sent" and latest.checkin_status == CHECKIN_PENDING:
            logger.info("hair_care_plan_skipped_checkin_pending", user_id=user.id, day_number=latest.day_number)
            await db.commit()
            return

        # Prior generated/delivery-failed routines get priority; don't build a backlog
        # of daily plans the user has not received yet.
        if latest and latest.content and latest.delivery_status in {"awaiting_window", "pending", "failed"}:
            plan_id = latest.id
            snapshot = None
            should_send = True
        elif latest and latest.delivery_status in {"generating", "failed"} and not latest.content:
            if latest.delivery_status == "generating":
                created_at = _as_utc(latest.created_at)
                if created_at and now - created_at <= _GENERATION_STALE_AFTER:
                    await db.commit()
                    return
            latest.delivery_status = "generating"
            latest.last_send_error = None
            plan_id = latest.id
            snapshot = {
                "profile": _hair_plan_profile(user),
                "day_number": latest.day_number,
                "recent_plans": await _recent_plans(db, user.id, exclude_id=latest.id),
            }
            await db.commit()
            should_send = False
        else:
            today_plan = await db.scalar(select(HairCarePlan).where(
                HairCarePlan.user_id == user.id, HairCarePlan.plan_date == today
            ).with_for_update())
            if today_plan:
                if today_plan.delivery_status in {"sent", "sending", "unknown"}:
                    await db.commit()
                    return
                if today_plan.content:
                    plan_id = today_plan.id
                    snapshot = None
                    should_send = today_plan.delivery_status in {"pending", "failed", "awaiting_window"}
                else:
                    if today_plan.delivery_status == "generating":
                        created_at = _as_utc(today_plan.created_at)
                        if created_at and now - created_at <= _GENERATION_STALE_AFTER:
                            await db.commit()
                            return
                    today_plan.delivery_status = "generating"
                    today_plan.subscription_id = subscription.id
                    today_plan.last_send_error = None
                    plan_id = today_plan.id
                    snapshot = {
                        "profile": _hair_plan_profile(user),
                        "day_number": today_plan.day_number,
                        "recent_plans": await _recent_plans(db, user.id, exclude_id=today_plan.id),
                    }
                    await db.commit()
                    should_send = False
            else:
                # If yesterday's routine was already completed/skipped, today's new
                # routine can now be created. Failed/empty old rows keep their day id.
                max_day = await db.scalar(select(func.max(HairCarePlan.day_number)).where(
                    HairCarePlan.user_id == user.id
                ))
                today_plan = HairCarePlan(
                    user_id=user.id,
                    subscription_id=subscription.id,
                    plan_date=today,
                    day_number=(max_day or 0) + 1,
                    content="",
                    delivery_status="generating",
                    send_attempts=0,
                )
                db.add(today_plan)
                await db.flush()
                plan_id = today_plan.id
                snapshot = {
                    "profile": _hair_plan_profile(user),
                    "day_number": today_plan.day_number,
                    "recent_plans": await _recent_plans(db, user.id, exclude_id=today_plan.id),
                }
                await db.commit()
                should_send = False

    if snapshot is not None:
        try:
            content = await generate_hair_care_plan(
                snapshot["profile"], snapshot["recent_plans"],
                summary=None, day_number=snapshot["day_number"],
            )
        except Exception as exc:
            await _mark_status(plan_id, "failed", error=f"Generation failed: {type(exc).__name__}: {exc}")
            raise
        async with AsyncSessionLocal() as db:
            plan = await db.get(HairCarePlan, plan_id)
            if not plan:
                return
            if plan.delivery_status != "generating":
                logger.warning("hair_care_plan_generation_state_changed", user_id=user_id, plan_id=plan_id)
                return
            plan.content = content
            plan.delivery_status = "pending"
            plan.last_send_error = None
            await db.commit()
        should_send = True

    if should_send:
        await _send_plan(user_id, plan_id)
        logger.info("hair_care_plan_processed", user_id=user_id, plan_id=plan_id)


async def generate_and_send_daily_plans() -> None:
    """Queue one idempotent daily job for each eligible subscribed hair profile."""
    if settings.hair_care_launch_hold:
        logger.info("hair_care_launch_hold_daily_generation_skipped")
        return
    from app.redis_client import get_arq_pool
    from app.models import Subscription

    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User.id, User.age, User.medical_conditions, User.hair_onboarding_complete)
            .join(Subscription, Subscription.user_id == User.id)
            .where(
                User.hair_onboarding_complete.is_(True),
                User.age >= MIN_ROUTINE_AGE,
                User.age <= MAX_ROUTINE_AGE,
                Subscription.status == "active",
                Subscription.start_date <= now,
                Subscription.end_date > now,
            ).distinct().order_by(User.id)
        )
        user_ids = [
            row.id for row in result.all()
            if routine_eligibility_block_reason(
                age=row.age,
                medical_conditions=row.medical_conditions,
                hair_onboarding_complete=row.hair_onboarding_complete,
            ) is None
        ]

    pool = await get_arq_pool()
    local_date = datetime.now(IST).date().isoformat()
    queued = 0
    for user_id in user_ids:
        job = await pool.enqueue_job(
            "generate_hair_care_plan_for_user", user_id,
            _job_id=f"hair_plan:{user_id}:{local_date}",
        )
        if job is not None:
            queued += 1
    logger.info("daily_hair_care_plan_jobs_queued", eligible=len(user_ids), queued=queued)


async def retry_pending_checkin_prompts_job() -> None:
    """ARQ cron target; failed prompts are retried only if Meta's window is open."""
    await retry_pending_checkin_prompts()


async def revise_today_plan(db: AsyncSession, user: User, modification_instruction: str) -> PlanRevisionResult:
    if settings.hair_care_launch_hold or not modification_instruction.strip() or not is_routine_eligible_user(user):
        return PlanRevisionResult(plan=None)
    today = _plan_day_utc()
    plan = await db.scalar(select(HairCarePlan).where(
        HairCarePlan.user_id == user.id, HairCarePlan.plan_date == today
    ).with_for_update())
    if not plan or not plan.content or plan.delivery_status in {"generating", "sending", "unknown"}:
        return PlanRevisionResult(plan=None)

    now = datetime.now(timezone.utc)
    if is_duplicate_modification(plan.last_modification_instruction, plan.last_modified_at, modification_instruction, now):
        return PlanRevisionResult(plan=plan, is_duplicate=True)

    recent = await _recent_plans(db, user.id, exclude_id=plan.id)
    profile = _hair_plan_profile(user)
    current_content = plan.content
    day_number = plan.day_number
    new_content = await generate_hair_care_plan(
        profile, recent, day_number=day_number,
        modification_instruction=modification_instruction,
        current_plan_text=current_content,
    )
    plan.content = new_content
    plan.delivery_status = "pending"
    plan.sent_at = None
    plan.send_attempts = 0
    plan.last_send_error = None
    plan.provider_message_id = None
    plan.last_modification_instruction = modification_instruction
    plan.last_modified_at = now
    await db.commit()
    await db.refresh(plan)
    return PlanRevisionResult(plan=plan)


async def get_historical_plan(db, user_id: int, *, day_number: int | None = None, local_date=None) -> HairCarePlan | None:
    if day_number is not None:
        return await db.scalar(select(HairCarePlan).where(
            HairCarePlan.user_id == user_id, HairCarePlan.day_number == day_number
        ))
    if local_date is not None:
        return await db.scalar(select(HairCarePlan).where(
            HairCarePlan.user_id == user_id, HairCarePlan.plan_date == _canonical_day_from_local_date(local_date)
        ))
    return None
