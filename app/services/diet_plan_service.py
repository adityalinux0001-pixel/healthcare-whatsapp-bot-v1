"""Daily personalized diet/exercise plan lifecycle."""
from __future__ import annotations

from datetime import datetime, timezone, time
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import RetryError

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import User, DietPlan, Message
from app.llm.gemini_client import generate_diet_plan
from app.whatsapp.client import send_text_message, send_template_message
from app.utils.logging_config import logger

IST = ZoneInfo("Asia/Kolkata")


def _plan_day_utc() -> datetime:
    local_date = datetime.now(IST).date()
    return datetime.combine(local_date, time.min, tzinfo=IST).astimezone(timezone.utc)


def _canonical_day_from_local_date(local_date) -> datetime:
    return datetime.combine(local_date, time.min, tzinfo=IST).astimezone(timezone.utc)


def _template_plan_content(content: str, max_chars: int = 880) -> str:
    """Create a compact single-line version for WhatsApp template delivery.

    The complete generated plan remains unchanged in DietPlan.content.
    This helper only formats the version sent through the approved
    WhatsApp template.
    """
    first_line, separator, remainder = content.partition("\n")

    if first_line.strip().replace("*", "").startswith("🌿 Day ") and separator:
        content = remainder.lstrip("\n")

    # The general-wellness disclaimer is intentionally omitted from the
    # compact template version. The full saved plan still contains it.
    disclaimer_marker = "⚠️ This plan is for general wellness."
    if disclaimer_marker in content:
        content = content.split(disclaimer_marker, 1)[0].rstrip()

    # WhatsApp template parameters cannot contain newlines or tabs.
    content = " ".join(content.split())

    # Keep the dynamic parameter comfortably below Meta's 1024-character
    # total template-body limit, leaving room for the template's own text.
    if len(content) > max_chars:
        content = content[: max_chars - 3].rsplit(" ", 1)[0].rstrip() + "..."

    return content


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
    # window. When it is closed (such as a proactive 06:00 IST daily send), use
    # the user's approved utility template instead.
    within_service_window = bool(
        last_inbound_at
        and (datetime.now(timezone.utc) - last_inbound_at.astimezone(timezone.utc)).total_seconds()
        < 24 * 60 * 60
    )
    use_text = within_service_window
    template_name = settings.whatsapp_daily_plan_template_name.strip()

    if not use_text and not template_name:
        error = (
            "WhatsApp 24-hour service window is closed and "
            "WHATSAPP_DAILY_PLAN_TEMPLATE_NAME is not configured."
        )
        await _mark_delivery(plan_id, "failed", error=error)
        logger.error(
            "diet_plan_delivery_blocked_template_missing",
            user_id=user.id,
            day_number=day_number,
        )
        return

    try:
        if use_text:
            response = await send_text_message(user.phone_number, content)
        else:
            # Template created in Meta: `daily_diet_plan`
            # {{1}} = day number, {{2}} = compact rendered plan body.
            #
            # The stored DietPlan.content remains the complete generated plan.
            # Only the template-delivered version is compacted because Meta
            # rejects template parameters containing newlines/tabs and the
            # complete plan can exceed the template's 1024-character limit.
            template_content = _template_plan_content(content)

            response = await send_template_message(
                user.phone_number,
                template_name,
                settings.whatsapp_daily_plan_template_language_code,
                components=[{
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": str(day_number)},
                        {"type": "text", "text": template_content},
                    ],
                }],
            )
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


async def generate_plan_for_user(user_id: int, *, prefer_text: bool = False) -> None:
    """Create exactly one plan per user/IST day; retries never regenerate a saved plan."""
    from app.services.subscription_service import get_active_subscription
    from app.knowledge.safety import detect_high_risk_profile

    async with AsyncSessionLocal() as db:
        user: User | None = await db.get(User, user_id)
        if not user or not user.onboarding_complete:
            return

        if user.age is not None and user.age < 18:
            logger.warning("daily_plan_skipped_minor", user_id=user_id)
            return

        sub = await get_active_subscription(db, user)
        if not sub:
            return

        plan, created = await _claim_today_plan(db, user, sub.id)

        if plan.delivery_status in {"sent", "unknown"}:
            return

        if not created and plan.content:
            await _send_plan(user, plan, prefer_text=prefer_text)
            return

        high_risk = detect_high_risk_profile(user.profile_dict())
        if high_risk:
            logger.warning(
                "daily_plan_blocked_high_risk_profile",
                user_id=user_id,
                reason=high_risk,
            )
            return

        recent = await _recent_meals(db, user_id)

        plan_text = await generate_diet_plan(
            user.profile_dict(),
            recent,
            summary=user.conversation_summary,
            day_number=plan.day_number,
        )

        plan.content = plan_text
        await db.commit()
        await db.refresh(plan)

    await _send_plan(user, plan, prefer_text=prefer_text)
    logger.info(
        "diet_plan_processed",
        user_id=user_id,
        day_number=plan.day_number,
        created=created,
    )


async def revise_today_plan(
    db: AsyncSession,
    user: User,
    modification_instruction: str,
) -> DietPlan | None:
    """Revise today's existing plan only when explicitly requested by the user."""
    from app.knowledge.safety import detect_high_risk_profile

    if not modification_instruction.strip():
        return None

    today = _plan_day_utc()

    plan = await db.scalar(
        select(DietPlan)
        .where(
            DietPlan.user_id == user.id,
            DietPlan.plan_date == today,
        )
        .with_for_update()
    )

    if not plan or not plan.content:
        return None

    # Do not modify a plan while delivery is in progress or its external outcome
    # is unknown. The explicit request can safely be retried later.
    if plan.delivery_status in {"sending", "unknown"}:
        return None

    high_risk = detect_high_risk_profile(user.profile_dict())
    if high_risk:
        logger.warning(
            "today_plan_revision_blocked_high_risk_profile",
            user_id=user.id,
            reason=high_risk,
        )
        return None

    recent = await _recent_meals(db, user.id)

    plan_text = await generate_diet_plan(
        user.profile_dict(),
        recent,
        summary=user.conversation_summary,
        day_number=plan.day_number,
        modification_instruction=modification_instruction,
    )

    # Preserve the same DietPlan row and day_number. Only today's content and
    # delivery bookkeeping are reset for the new user-requested version.
    plan.content = plan_text
    plan.delivery_status = "pending"
    plan.sent_at = None
    plan.send_attempts = 0
    plan.last_send_error = None
    plan.provider_message_id = None

    await db.commit()
    await db.refresh(plan)

    logger.info(
        "today_plan_revised",
        user_id=user.id,
        day_number=plan.day_number,
    )

    return plan


async def generate_and_send_daily_plans() -> None:
    from app.redis_client import get_arq_pool

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User.id).where(
                User.onboarding_complete.is_(True),
                User.age >= 18,
            )
        )
        user_ids = [row[0] for row in result.all()]

    pool = await get_arq_pool()

    for uid in user_ids:
        await pool.enqueue_job("generate_diet_plan_for_user", uid)

    logger.info("daily_plan_jobs_queued", count=len(user_ids))


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