
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import RetryError

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import User, DietPlan, Message
# The legacy generation implementation is retired. Keep the ORM/read helpers below
# for historical DietPlan rows, but never import or invoke the old model generator.
from app.services.plan_revision_policy import is_duplicate_modification
from app.whatsapp.client import send_text_message, send_reply_buttons
from app.utils.logging_config import logger


@dataclass
class PlanRevisionResult:
    """Result of a same-day plan revision attempt.

    is_duplicate=True means the existing plan was reused as-is because the
    instruction matched one already applied a short while ago -- the caller
    should say so rather than claiming a fresh update just happened.
    """
    plan: DietPlan | None
    is_duplicate: bool = False

IST = ZoneInfo("Asia/Kolkata")


def _plan_day_utc() -> datetime:
    local_date = datetime.now(IST).date()
    return datetime.combine(local_date, time.min, tzinfo=IST).astimezone(timezone.utc)


def _canonical_day_from_local_date(local_date) -> datetime:
    return datetime.combine(local_date, time.min, tzinfo=IST).astimezone(timezone.utc)


# --------------------------------------------------------------------------
# Daily check-in + 24h free-form window (no paid WhatsApp templates anywhere)
# --------------------------------------------------------------------------
# WhatsApp allows free-form (non-template) messages for 24h after the user's

SERVICE_WINDOW_SECONDS = 24 * 60 * 60
PLAN_SEND_HOUR_IST = 6  # same hour as the daily cron in app/worker.py

CHECKIN_PENDING = "pending"
CHECKIN_BUTTON_PREFIX = "chk"
# button/choice code -> value stored in DietPlan.checkin_status
CHECKIN_CHOICES = {"done": "done", "not_done": "not_done", "skip": "skipped"}
CHECKIN_ANSWERED = frozenset(CHECKIN_CHOICES.values())
CHECKIN_BUTTON_TITLES = {"done": "✅ Done", "not_done": "❌ Not done", "skip": "⏭️ Skip"}

# Conservative exact-match text fallbacks, so a user who types instead of tapping
# is not stuck behind the mandatory check-in.
_CHECKIN_TEXT_ANSWERS = {
    "done": "done", "done!": "done", "ho gaya": "done", "hogaya": "done", "ho gya": "done",
    "hogya": "done", "done ho gaya": "done", "completed": "done",
    "not done": "not_done", "nahi hua": "not_done", "nhi hua": "not_done",
    "nahi kiya": "not_done", "nhi kiya": "not_done", "not done yet": "not_done",
    "skip": "skip", "skipped": "skip", "skip it": "skip", "skip kar do": "skip",
}


def checkin_button_id(plan_id: int, choice: str) -> str:
    return f"{CHECKIN_BUTTON_PREFIX}:{plan_id}:{choice}"


def parse_checkin_button_id(button_id: str | None) -> tuple[int, str] | None:
    """'chk:<plan_id>:<choice>' -> (plan_id, choice); anything else -> None."""
    parts = (button_id or "").split(":")
    if len(parts) != 3 or parts[0] != CHECKIN_BUTTON_PREFIX:
        return None
    if not parts[1].isdigit() or parts[2] not in CHECKIN_CHOICES:
        return None
    return int(parts[1]), parts[2]


def parse_checkin_text(text: str) -> str | None:
    normalized = " ".join(text.casefold().replace("✅", " ").replace("❌", " ").replace("⏭️", " ").split())
    return _CHECKIN_TEXT_ANSWERS.get(normalized)


def _service_window_open(last_inbound_at: datetime | None) -> bool:
    return bool(
        last_inbound_at
        and (datetime.now(timezone.utc) - last_inbound_at.astimezone(timezone.utc)).total_seconds()
        < SERVICE_WINDOW_SECONDS
    )


async def _latest_plan(db, user_id: int) -> DietPlan | None:
    return await db.scalar(
        select(DietPlan)
        .where(DietPlan.user_id == user_id)
        .order_by(DietPlan.day_number.desc())
        .limit(1)
    )


async def get_pending_checkin_plan(db, user_id: int) -> DietPlan | None:
    """Latest plan if it was delivered and the user has not answered its check-in."""
    plan = await _latest_plan(db, user_id)
    if plan and plan.delivery_status == "sent" and plan.checkin_status == CHECKIN_PENDING:
        return plan
    return None


async def get_unanswered_prior_day_checkin(db, user_id: int) -> DietPlan | None:
    """Pending check-in from a previous day (today's is still open and not nagged)."""
    plan = await get_pending_checkin_plan(db, user_id)
    if plan and plan.plan_date < _plan_day_utc():
        return plan
    return None


async def needs_plan_resume(db, user: User) -> bool:
  
    from app.knowledge.safety import detect_high_risk_profile

    if not user.onboarding_complete or (user.age is not None and user.age < 18):
        return False

    if detect_high_risk_profile(user.profile_dict()):
        return False

    latest = await _latest_plan(db, user.id)
    if latest and latest.delivery_status == "awaiting_window":
        return True  # a generated plan is waiting to be delivered

    today_plan = await db.scalar(
        select(DietPlan.id).where(DietPlan.user_id == user.id, DietPlan.plan_date == _plan_day_utc())
    )
    if today_plan is not None:
        return False
    if latest and latest.delivery_status == "sent" and latest.checkin_status == CHECKIN_PENDING:
        return False  # blocked until the user answers the check-in
    # Before 06:00 IST the normal cron will create today's plan.
    return datetime.now(IST).hour >= PLAN_SEND_HOUR_IST


async def send_checkin_prompt(phone: str, plan_id: int, day_number: int, *, reminder: bool = False) -> None:
    """Send the Done / Not done / Skip buttons for a plan."""
    if reminder:
        body = (
            f"👋 Quick check-in first!\n\n"
            f"Did you follow your *Day {day_number}* diet & exercise plan?\n\n"
            "Tap one option below — I'll send your next plan right after 👇"
        )
    else:
        body = (
            f"✅ *Day {day_number} check-in*\n\n"
            "Did you follow today's diet & exercise plan?\n\n"
            "Tap one option below — your next plan is sent after you reply 👇"
        )
    await send_reply_buttons(
        phone,
        body,
        [(checkin_button_id(plan_id, choice), CHECKIN_BUTTON_TITLES[choice]) for choice in CHECKIN_CHOICES],
    )


async def _start_checkin(user: User, plan_id: int, day_number: int) -> None:

    try:
        async with AsyncSessionLocal() as db:
            plan = await db.get(DietPlan, plan_id)
            if not plan or plan.checkin_status in CHECKIN_ANSWERED:
                return  # e.g. a revised plan whose check-in was already answered
            if plan.checkin_status != CHECKIN_PENDING:
                plan.checkin_status = CHECKIN_PENDING
                plan.checkin_sent_at = datetime.now(timezone.utc)
                await db.commit()
        await send_checkin_prompt(user.phone_number, plan_id, day_number)
    except Exception:
        logger.exception("diet_plan_checkin_prompt_failed", user_id=user.id, plan_id=plan_id)


async def record_checkin(db, user: User, plan_id: int, choice: str) -> tuple[str, DietPlan | None]:
    """Store the user's answer. Returns ('recorded'|'already'|'not_found', plan)."""
    plan = await db.scalar(
        select(DietPlan)
        .where(DietPlan.id == plan_id, DietPlan.user_id == user.id)
        .with_for_update()
    )
    if not plan:
        await db.commit()
        return "not_found", None
    if plan.checkin_status in CHECKIN_ANSWERED:
        await db.commit()
        return "already", plan
    plan.checkin_status = CHECKIN_CHOICES[choice]
    plan.checkin_responded_at = datetime.now(timezone.utc)
    await db.commit()
    return "recorded", plan


async def _recent_meals(db, user_id: int) -> list[str]:
    result = await db.execute(
        select(DietPlan.content)
        .where(
            DietPlan.user_id == user_id,
            DietPlan.content != "",
            DietPlan.delivery_status.in_(["pending", "sent", "failed", "unknown"]),
        )
        .order_by(DietPlan.plan_date.desc())
        .limit(3)
    )
    return list(result.scalars().all())


async def _claim_today_plan(db, user: User, subscription_id: int) -> tuple[DietPlan, bool]:
    """Serialize plan creation per user and reserve today's DB row before LLM work."""
    today = _plan_day_utc()
    await db.execute(select(User.id).where(User.id == user.id).with_for_update())

    existing = await db.scalar(
        select(DietPlan).where(DietPlan.user_id == user.id, DietPlan.plan_date == today)
    )
    if existing:
        await db.commit()
        return existing, False

    max_day = await db.scalar(select(func.max(DietPlan.day_number)).where(DietPlan.user_id == user.id))
    plan = DietPlan(
        user_id=user.id,
        subscription_id=subscription_id,
        plan_date=today,
        day_number=(max_day or 0) + 1,
        content="",
        delivery_status="pending",
        send_attempts=0,
    )
    db.add(plan)
    await db.flush()
    return plan, True


async def _last_inbound_at(db, user_id: int) -> datetime | None:
    """Return the latest persisted inbound WhatsApp message time for this user."""
    return await db.scalar(
        select(Message.created_at)
        .where(Message.user_id == user_id, Message.role == "user")
        .order_by(Message.created_at.desc())
        .limit(1)
    )


async def _claim_delivery(db, plan_id: int) -> tuple[DietPlan | None, bool]:
    plan = await db.scalar(select(DietPlan).where(DietPlan.id == plan_id).with_for_update())
    if not plan or not plan.content:
        return plan, False
    if plan.delivery_status in {"sent", "sending", "unknown"}:
        return plan, False
    plan.delivery_status = "sending"
    plan.send_attempts += 1
    plan.last_send_error = None
    await db.commit()
    return plan, True


async def _mark_delivery(
    plan_id: int,
    status: str,
    *,
    error: str | None = None,
    provider_message_id: str | None = None,
) -> None:
    async with AsyncSessionLocal() as db:
        plan = await db.get(DietPlan, plan_id)
        if not plan:
            return
        plan.delivery_status = status
        plan.last_send_error = error[:2000] if error else None
        if provider_message_id:
            plan.provider_message_id = provider_message_id
        if status == "sent":
            plan.sent_at = datetime.now(timezone.utc)
        await db.commit()


async def _send_plan(user: User, plan: DietPlan, *, prefer_text: bool = False) -> None:
    async with AsyncSessionLocal() as db:
        current, claimed = await _claim_delivery(db, plan.id)
        if not current or not claimed:
            return
        content = current.content
        day_number = current.day_number
        plan_id = current.id
        last_inbound_at = await _last_inbound_at(db, user.id)

    # Free-form WhatsApp text is valid only inside the 24-hour customer-service
    # window. We never use (chargeable) templates: if the window is closed the
    # generated plan is parked as "awaiting_window" and delivered automatically
    # the next time the user messages us (see needs_plan_resume / worker job).
    if not _service_window_open(last_inbound_at):
        await _mark_delivery(
            plan_id,
            "awaiting_window",
            error="WhatsApp 24h window closed; will deliver when the user next messages.",
        )
        logger.warning("diet_plan_delivery_deferred_window_closed", user_id=user.id, day_number=day_number)
        return

    try:
        response = await send_text_message(user.phone_number, content)
    except RetryError as exc:
        root = exc.last_attempt.exception()

        # Transport/server failures can have an ambiguous outcome. Do not blindly
        # resend an unknown external side effect; mark it for reconciliation.
        if isinstance(root, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
            await _mark_delivery(
                plan_id,
                "unknown",
                error="Ambiguous WhatsApp delivery outcome after retries; manual/provider-status reconciliation required.",
            )
            logger.error("diet_plan_delivery_unknown", user_id=user.id, day_number=day_number)
            return

        if isinstance(root, httpx.HTTPStatusError) and root.response.status_code >= 500:
            await _mark_delivery(
                plan_id,
                "unknown",
                error=f"WhatsApp server error: {root.response.status_code}",
            )
            return

        await _mark_delivery(plan_id, "failed", error=str(root))
        raise

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code >= 500 or exc.response.status_code == 429:
            await _mark_delivery(
                plan_id,
                "unknown",
                error=f"WhatsApp transport error: {exc.response.status_code}",
            )
        else:
            await _mark_delivery(plan_id, "failed", error=str(exc))
        raise

    except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError):
        await _mark_delivery(
            plan_id,
            "unknown",
            error="Ambiguous WhatsApp delivery outcome; manual/provider-status reconciliation required.",
        )
        return

    except Exception as exc:
        await _mark_delivery(plan_id, "failed", error=str(exc))
        raise

    provider_message_id = None
    try:
        provider_message_id = response.get("messages", [{}])[0].get("id")
    except (AttributeError, IndexError, TypeError):
        pass

    await _mark_delivery(plan_id, "sent", provider_message_id=provider_message_id)
    # Ask whether the user followed this plan (also keeps the 24h window open).
    await _start_checkin(user, plan_id, day_number)


async def generate_plan_for_user(user_id: int, *, prefer_text: bool = False) -> None:
    """Deprecated compatibility handler; daily diet plans are retired.

    This no-op is intentionally kept under the old function name so delayed ARQ jobs
    from a rolling deployment cannot generate or send a weight-loss plan to a hair-assistant
    user. Historical DietPlan rows are not modified or deleted.
    """
    logger.warning("legacy_diet_plan_job_skipped_hair_assistant", user_id=user_id)
    return


async def revise_today_plan(
    db: AsyncSession,
    user: User,
    modification_instruction: str,
) -> PlanRevisionResult:
    """Deprecated safety guard: legacy diet-plan revision is disabled.

    Keep the function signature for compatibility with already-deployed code, but
    never modify historical diet plans after the product has switched to hair/scalp
    guidance. Existing plan rows remain available for audit/history.
    """
    logger.warning("legacy_diet_plan_revision_skipped_hair_assistant", user_id=user.id)
    return PlanRevisionResult(plan=None)


async def generate_and_send_daily_plans() -> None:
    """Deprecated no-op retained for compatibility with older scheduled invocations."""
    logger.info("legacy_daily_diet_plan_scheduler_disabled")
    return


async def get_historical_plan(
    db,
    user_id: int,
    *,
    day_number: int | None = None,
    local_date=None,
) -> DietPlan | None:
    if day_number is not None:
        return await db.scalar(
            select(DietPlan).where(
                DietPlan.user_id == user_id,
                DietPlan.day_number == day_number,
            )
        )

    if local_date is not None:
        return await db.scalar(
            select(DietPlan).where(
                DietPlan.user_id == user_id,
                DietPlan.plan_date == _canonical_day_from_local_date(local_date),
            )
        )

    return None