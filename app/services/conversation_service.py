from sqlalchemy import select
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import re
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Message
from app.config import settings
from app.services.subscription_service import get_active_subscription, prompt_payment
from app.llm.gemini_client import run_general_qa, update_conversation_summary
from app.llm.context import build_response_context
from app.knowledge.safety import (
    consent_request_message, consent_declined_message, detect_red_flag, emergency_response,
    detect_high_risk_profile, high_risk_profile_message,
)
from app.whatsapp.client import send_text_message
from app.redis_client import redis_client
from app.utils.logging_config import logger
from app.utils.security import mask_identifier
from app.services.profile_validation import validate_extracted_fields
from app.services.onboarding_extract import (
    ONBOARDING_ORDER, QUESTIONS, RETRY_HINTS, extract_fields_from_text,
)

HISTORY_LIMIT = 8        # messages sent to Gemini each turn
SUMMARY_EVERY_N = 20     # update long-term summary every N user messages
CHECKIN_NUDGE_COOLDOWN_SECONDS = 3 * 60 * 60  # re-ask a pending check-in at most this often

# Production-safe per-user daily inbound-message limit.
# The reset is based on India Standard Time (IST), which keeps the limit aligned
# with the bot's primary operating timezone without changing the DB schema.
DAILY_MESSAGE_LIMIT = 25
DAILY_MESSAGE_TIMEZONE = ZoneInfo("Asia/Kolkata")
DAILY_MESSAGE_LIMIT_MESSAGE = (
    "Your daily message limit has been reached. "
    "Your limit will reset tomorrow at 12:00 AM IST. "
    "You can message again after the reset. 🙂"
)


async def _get_or_create_user(db: AsyncSession, phone: str) -> User:
    user = await db.scalar(select(User).where(User.phone_number == phone))
    if not user:
        user = User(phone_number=phone)
        db.add(user)
        await db.flush()
    return user


async def _recent_history(db: AsyncSession, user: User, exclude_whatsapp_message_id: str | None = None) -> list[dict]:
    query = select(Message).where(Message.user_id == user.id)
    if exclude_whatsapp_message_id:
        query = query.where(Message.whatsapp_message_id != exclude_whatsapp_message_id)
    result = await db.execute(
        query.order_by(Message.created_at.desc()).limit(HISTORY_LIMIT)
    )
    rows = list(reversed(result.scalars().all()))
    return [{"role": m.role, "content": m.content} for m in rows]


async def _user_message_count(db: AsyncSession, user: User) -> int:
    from sqlalchemy import func
    result = await db.execute(
        select(func.count()).select_from(Message).where(
            Message.user_id == user.id,
            Message.role == "user",
        )
    )
    return result.scalar() or 0


async def _today_user_message_count(db: AsyncSession, user: User) -> int:
    """Count this user's stored inbound messages for the current IST day."""
    from sqlalchemy import func

    now_ist = datetime.now(DAILY_MESSAGE_TIMEZONE)
    day_start_ist = now_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_ist = day_start_ist + timedelta(days=1)

    result = await db.execute(
        select(func.count()).select_from(Message).where(
            Message.user_id == user.id,
            Message.role == "user",
            Message.created_at >= day_start_ist,
            Message.created_at < day_end_ist,
        )
    )
    return int(result.scalar() or 0)


async def handle_incoming_message(
    db: AsyncSession, phone: str, text: str, wa_message_id: str,
    button_id: str | None = None,
) -> None:
    # FIX: Per-user distributed lock — prevents two workers processing messages
    # from the same user simultaneously (race on profile fields).
    lock_key = f"conv_lock:{phone}"
    lock_token = uuid4().hex
    lock_acquired = await redis_client.set(
        lock_key, lock_token, nx=True, ex=settings.conversation_lock_seconds
    )
    if not lock_acquired:
        # Another worker is processing a message from this user right now.
        # Re-enqueue and let the next retry pick it up (arq will retry with backoff).
        logger.warning("conv_lock_busy", phone=mask_identifier(phone))
        raise RuntimeError("Conversation lock busy — will retry")

    try:
        user = await _get_or_create_user(db, phone)

        # Idempotency backstop: webhook retries or duplicate queue jobs for the
        # same Meta message must never trigger a second Gemini reply.
        existing_message = await db.scalar(
            select(Message).where(Message.whatsapp_message_id == wa_message_id)
        )
        if existing_message:
            logger.info("duplicate_whatsapp_message_skipped", phone=mask_identifier(phone), wa_message_id=wa_message_id)
            return

        # Safety must run before payment gating and before storing an unconsented
        # health message. The emergency response itself is intentionally generic.
        if detect_red_flag(text):
            # Safety responses remain available even after the daily quota is reached.
            await send_text_message(phone, emergency_response())
            return

        # Daily quota: enforce before persistence/payment/LLM work so a user cannot
        # consume more than 25 normal inbound messages in one IST calendar day.
        # The existing per-user Redis lock makes the DB count check serial per user,
        # so concurrent webhook jobs cannot both pass the 25-message boundary.
        today_message_count = await _today_user_message_count(db, user)
        if today_message_count >= DAILY_MESSAGE_LIMIT:
            logger.info(
                "daily_message_limit_reached",
                user_id=user.id,
                phone=mask_identifier(phone),
                daily_message_count=today_message_count,
                daily_message_limit=DAILY_MESSAGE_LIMIT,
            )

            # Every normal message after the daily limit is reached gets the same
            # clear reminder. The message itself is not persisted, so it cannot
            # increase the daily user-message count or affect normal conversation flow.
            await send_text_message(phone, DAILY_MESSAGE_LIMIT_MESSAGE)
            return

        # Consent boundary: do not persist arbitrary inbound content until consent
        # exists. Consent responses themselves contain no health profile data.
        if settings.require_health_consent and user.health_data_consent_at is None:
            normalized = text.strip().casefold()
            if normalized in {"yes", "y", "agree", "accept", "i agree"}:
                from datetime import datetime, timezone
                user.health_data_consent_at = datetime.now(timezone.utc)
                db.add(Message(user_id=user.id, role="user", content=text, whatsapp_message_id=wa_message_id))
                await db.flush()
                reply = f"Thank you. ✅ Consent recorded.\n\n{QUESTIONS[ONBOARDING_ORDER[0]]}"
                db.add(Message(user_id=user.id, role="assistant", content=reply))
                await db.commit()
                await send_text_message(phone, reply)
            else:
                reply = consent_declined_message() if normalized in {"no", "n"} else consent_request_message()
                db.add(Message(user_id=user.id, role="assistant", content=reply))
                await db.commit()
                await send_text_message(phone, reply)
            return

        # Consent exists: now it is safe to persist the inbound health/profile message.
        #
        # Payment gate: once onboarding is complete, an active subscription is required
        # before any normal conversation is processed. This check happens before the
        # inbound message is persisted and before Gemini is called, so unpaid users
        # cannot consume normal chatbot functionality.
        #
        # Safety/red-flag handling above intentionally remains available regardless of
        # payment status so genuine emergency messages still receive a safety response.
        if user.onboarding_complete:
            subscription = await get_active_subscription(db, user)
            if not subscription:
                high_risk = detect_high_risk_profile(user.profile_dict())
                if high_risk:
                    logger.warning(
                        "payment_prompt_blocked_high_risk_profile",
                        user_id=user.id,
                        phone=mask_identifier(phone),
                        reason=high_risk,
                    )
                    await send_text_message(phone, high_risk_profile_message())
                    return
                await prompt_payment(db, user)
                logger.info(
                    "conversation_payment_required",
                    user_id=user.id,
                    phone=mask_identifier(phone),
                )
                return

        db.add(Message(
            user_id=user.id,
            role="user",
            content=text,
            whatsapp_message_id=wa_message_id,
        ))
        await db.commit()

        # Tapped a quick-reply button (daily check-in). Handled deterministically:
        # never routed through onboarding or the LLM. The inbound message above is
        # already persisted, which is what keeps the 24h free-form window open.
        # _handle_button_reply enqueues the plan resume itself if needed, so we
        # return immediately — _continue_plan_flow must NOT also enqueue it.
        if button_id:
            await _handle_button_reply(db, user, phone, button_id)
            return

        history = await _recent_history(db, user, exclude_whatsapp_message_id=wa_message_id)
        summary = user.conversation_summary

        if not user.onboarding_complete:
            await _handle_onboarding(db, user, phone, text, history, summary)
        else:
            handled_checkin = await _try_text_checkin_answer(db, user, phone, text)
            if handled_checkin:
                # _record_and_reply_checkin already enqueues the plan resume.
                # Do NOT also call _continue_plan_flow — that would enqueue a second job
                # and cause the plan to be sent twice (the awaiting_window delivery race).
                pass
            else:
                handled_ack = await _send_simple_acknowledgement(db, user, phone, text)
                if not handled_ack:
                    plan_delivered_from_db = await _handle_general_qa(db, user, phone, text, history, summary)
                else:
                    plan_delivered_from_db = False
                # Skip _continue_plan_flow when the plan was already delivered from DB
                # in this turn — otherwise the same plan is sent a second time.
                if not plan_delivered_from_db:
                    await _continue_plan_flow(db, user, phone)

        # FIX: Update long-term summary every N messages (background, non-blocking)
        msg_count = await _user_message_count(db, user)
        if msg_count > 0 and msg_count % SUMMARY_EVERY_N == 0:
            all_recent = await _recent_history(db, user)
            new_summary = await update_conversation_summary(summary, all_recent)
            user.conversation_summary = new_summary
            await db.commit()

    finally:
        # Delete only our own lock. A blind DEL can remove a newer owner's lock
        # if the old TTL expires during a slow LLM call.
        release_script = (
            "if redis.call('GET', KEYS[1]) == ARGV[1] "
            "then return redis.call('DEL', KEYS[1]) else return 0 end"
        )
        await redis_client.eval(release_script, 1, lock_key, lock_token)



_CHECKIN_ACK = {
    "done": "Awesome, great job! 💪 Day {n} marked as done.",
    "not_done": "No worries — every day is a fresh start! 🌿 Day {n} marked as not done.",
    "skip": "Okay, Day {n} skipped. ⏭️",
}


async def _enqueue_plan_resume(user: User) -> None:
    from app.redis_client import get_arq_pool

    pool = await get_arq_pool()
    # One resume job per user per day is enough; generate_plan_for_user is idempotent.
    await pool.enqueue_job(
        "generate_diet_plan_for_user", user.id, True,
        _job_id=f"resume_plan:{user.id}:{datetime.now(ZoneInfo('Asia/Kolkata')).date().isoformat()}",
    )


async def _continue_plan_flow(db: AsyncSession, user: User, phone: str) -> None:
    """After a normal chat turn: pick the daily plan flow up where it stopped.

    1. A previous day's check-in is still unanswered -> ask again (rate-limited).
    2. Otherwise, if a plan is waiting (24h window had closed) or today's plan was
       held back, deliver/generate it now. Never breaks the main chat flow.
    """
    try:
        from app.services import diet_plan_service as dps

        pending = await dps.get_unanswered_prior_day_checkin(db, user.id)
        if pending:
            nudge_key = f"checkin_nudge:{pending.id}"
            if await redis_client.set(nudge_key, "1", nx=True, ex=CHECKIN_NUDGE_COOLDOWN_SECONDS):
                await dps.send_checkin_prompt(phone, pending.id, pending.day_number, reminder=True)
            return

        if await dps.needs_plan_resume(db, user):
            await _enqueue_plan_resume(user)
    except Exception:
        logger.exception("plan_flow_continue_failed", user_id=user.id, phone=mask_identifier(phone))


async def _record_and_reply_checkin(
    db: AsyncSession, user: User, phone: str, plan_id: int, choice: str
) -> None:
    from app.services import diet_plan_service as dps

    result, plan = await dps.record_checkin(db, user, plan_id, choice)
    if result == "not_found" or plan is None:
        logger.info("checkin_plan_not_found", user_id=user.id, plan_id=plan_id)
        return

    if result == "already":
        reply = "✅ Already noted — thank you!"
        resume = False
    else:
        resume = False
        try:
            resume = await dps.needs_plan_resume(db, user)
        except Exception:
            logger.exception("checkin_resume_check_failed", user_id=user.id)
        tail = (
            "Preparing your next plan now… 🌿"
            if resume
            else "Your next plan will arrive at 6:00 AM IST. 🌿"
        )
        reply = f"{_CHECKIN_ACK[choice].format(n=plan.day_number)}\n\n{tail}"

    db.add(Message(user_id=user.id, role="assistant", content=reply))
    await db.commit()
    await send_text_message(phone, reply)

    if resume:
        try:
            await _enqueue_plan_resume(user)
        except Exception:
            logger.exception("checkin_resume_enqueue_failed", user_id=user.id)
    logger.info("checkin_recorded", user_id=user.id, plan_id=plan_id, choice=choice, result=result)


async def _handle_button_reply(db: AsyncSession, user: User, phone: str, button_id: str) -> None:
    from app.services.diet_plan_service import parse_checkin_button_id

    parsed = parse_checkin_button_id(button_id)
    if not parsed or not user.onboarding_complete:
        logger.info("button_reply_ignored", user_id=user.id, button_id=button_id[:40])
        return
    plan_id, choice = parsed
    await _record_and_reply_checkin(db, user, phone, plan_id, choice)


async def _try_text_checkin_answer(db: AsyncSession, user: User, phone: str, text: str) -> bool:
    """Accept a typed 'done' / 'not done' / 'skip' while a check-in is pending."""
    from app.services.diet_plan_service import parse_checkin_text, get_pending_checkin_plan

    choice = parse_checkin_text(text)
    if not choice:
        return False
    plan = await get_pending_checkin_plan(db, user.id)
    if not plan:
        return False
    await _record_and_reply_checkin(db, user, phone, plan.id, choice)
    return True


def _apply_extracted_fields(user: User, extracted: dict) -> None:
    """FIX: food_dislikes is APPENDED to, never blindly overwritten — the model
    is asked to send the combined list, but this is a belt-and-suspenders guard
    so an old dislike can never silently disappear because of one LLM turn."""
    extracted = validate_extracted_fields(extracted)
    for field, value in extracted.items():
        if not hasattr(user, field):
            continue
        if field == "food_dislikes" and value:
            existing = {d.strip().lower() for d in (user.food_dislikes or "").split(",") if d.strip()}
            new = {d.strip().lower() for d in str(value).split(",") if d.strip()}
            merged = existing | new
            if merged:
                setattr(user, field, ", ".join(sorted(merged)))
        else:
            setattr(user, field, value)
            if field == "allergies":
                user.allergies_answered = True
            elif field == "medical_conditions":
                user.medical_conditions_answered = True


async def _handle_onboarding(
    db: AsyncSession, user: User, phone: str, text: str,
    history: list[dict], summary: str | None,
) -> None:
   
    from app.redis_client import get_arq_pool

    missing = user.missing_fields()
    current_target = missing[0] if missing else None

    extracted: dict = {}
    if current_target:
        extracted = extract_fields_from_text(text, current_target, missing, history)
        if extracted:
            logger.info(
                "onboarding_fields_extracted",
                fields=sorted(extracted),
                target=current_target,
                user_id=user.id,
            )
        _apply_extracted_fields(user, extracted)

    # Keep personalized plans within the supported age range (12-75 years).
    # Do not enqueue a personalized plan outside this range.
    if user.age is not None and not 12 <= user.age <= 75:
        reply = (
            "This personalized diet and exercise service is available for users aged 12 to 75. "
            "If your age was extracted incorrectly, please provide your correct age."
        )
        db.add(Message(user_id=user.id, role="assistant", content=reply))
        await db.commit()
        await send_text_message(phone, reply)
        return

    missing_after = user.missing_fields()

    # The current target field is still unanswered -> hold position. Re-ask
    # the exact same question (with a short hint), do not advance, and do
    # not lose any *other* fields we may have opportunistically captured
    # above (they were already applied via _apply_extracted_fields).
    if current_target and current_target in missing_after:
        hint = RETRY_HINTS.get(current_target, "")
        question = QUESTIONS.get(current_target, "")
        reply = f"{hint}\n\n{question}".strip() if hint else question
        db.add(Message(user_id=user.id, role="assistant", content=reply))
        await db.commit()
        await send_text_message(phone, reply)
        return

    just_completed = (not user.onboarding_complete) and (not missing_after)
    just_completed_high_risk = False
    if just_completed:
        user.onboarding_complete = True
        high_risk = detect_high_risk_profile(user.profile_dict())
        if high_risk:
            just_completed_high_risk = True
            logger.warning(
                "onboarding_complete_high_risk_profile",
                user_id=user.id,
                reason=high_risk,
            )
            reply = high_risk_profile_message()
        else:
            reply = (
                "Perfect! ✅ Your profile is complete.\n\n"
                "Your personalized daily diet and exercise plan is now being generated based on "
                "your goals and preferences. 🌿"
            )
    else:
        next_field = missing_after[0]
        reply = QUESTIONS.get(next_field, "Could you share a bit more about yourself? 😊")

    db.add(Message(user_id=user.id, role="assistant", content=reply))
    await db.commit()
    await send_text_message(phone, reply)

    # Ask for payment only after onboarding is fully complete, and only when the
    # profile isn't flagged high-risk — a high-risk profile must never reach the
    # payment prompt or automated plan generation (see high_risk_profile_message()
    # sent above instead).
    if just_completed and not just_completed_high_risk:
        subscription = await get_active_subscription(db, user)
        if subscription:
            pool = await get_arq_pool()
            await pool.enqueue_job("generate_diet_plan_for_user", user.id, True)
            logger.info("onboarding_complete_plan_queued", user_id=user.id, prefer_text=True)
        else:
            await prompt_payment(db, user)
            logger.info("onboarding_complete_payment_prompted", user_id=user.id)



def _is_simple_acknowledgement(text: str) -> bool:
    """Return True only for standalone courtesy/acknowledgement messages."""
    normalized = re.sub(r"[^a-z0-9'!? ]+", " ", text.casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized in {
        "ok", "okay", "k", "thanks", "thank you", "thx",
        "ok thanks", "okay thanks", "thanks!", "okay!",
        "thank you!", "great", "perfect", "got it", "got it!",
    }


async def _send_simple_acknowledgement(
    db: AsyncSession, user: User, phone: str, text: str
) -> bool:
    """Handle pure acknowledgements without asking Gemini to restart the conversation."""
    if not _is_simple_acknowledgement(text):
        return False

    reply = "You're welcome! 😊"
    db.add(Message(user_id=user.id, role="assistant", content=reply))
    await db.commit()
    await send_text_message(phone, reply)
    return True


def _food_dislike_items(value: str | None) -> set[str]:
    if not value or value == "None reported":
        return set()
    return {item.strip().casefold() for item in re.split(r"[,;|]", value) if item.strip()}


def _format_profile_recall(user: User, fields: list[str]) -> str:
    """Render only profile facts explicitly requested by the user."""
    labels = {
        "name": "Name",
        "age": "Age",
        "gender": "Gender",
        "height_cm": "Height",
        "weight_kg": "Weight",
        "activity_level": "Activity level",
        "goal": "Goal",
        "diet_preference": "Diet preference",
        "allergies": "Allergies",
        "medical_conditions": "Medical conditions",
        "food_dislikes": "Food dislikes",
    }
    values = user.profile_dict()
    visible: list[str] = []
    for field in fields:
        value = values.get(field)
        if value in (None, ""):
            continue
        if field in {"height_cm", "weight_kg"}:
            suffix = " cm" if field == "height_cm" else " kg"
            value = f"{value}{suffix}"
        elif field in {"goal", "activity_level", "diet_preference"}:
            value = str(value).replace("_", " ")
        visible.append(f"{labels[field]}: {value}")

    if not visible:
        return "I don't have that detail saved yet."
    if len(visible) == 1:
        label, value = visible[0].split(":", 1)
        return f"Your saved {label.lower()} is {value.strip()}."
    return "Here are the details I currently have saved for you:\n" + "\n".join(f"• {item}" for item in visible)


def _trusted_profile_updates(route) -> dict:
    """Convert semantic extraction into validated DB fields, with a strict write gate."""
    raw: dict = {}
    for candidate in route.profile_updates:
        # LLM output is advisory. Persistence requires high confidence plus current-turn evidence.
        if candidate.confidence < settings.conversation_profile_update_min_confidence:
            continue
        if not candidate.evidence.strip() or not candidate.value.strip():
            continue
        raw[candidate.field] = candidate.value

    if not raw:
        return {}
    return validate_extracted_fields(raw)


def _resolve_plan_request(route, history: list[dict]) -> tuple[int | None, date | None, str, str | None]:
    """Resolve semantic plan references against the application clock; never trust model-supplied dates blindly."""
    from datetime import date as date_type

    reference = route.plan_reference
    day_number = route.plan_day_number
    plan_date = None

    if reference == "day_number":
        if not day_number or day_number < 1:
            return None, None, route.plan_scope, route.plan_section
        return int(day_number), None, route.plan_scope, route.plan_section

    if reference in {"today", "yesterday", "tomorrow"}:
        today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
        offsets = {"today": 0, "yesterday": -1, "tomorrow": 1}
        return None, today + timedelta(days=offsets[reference]), route.plan_scope, route.plan_section

    if reference == "date" and route.plan_date:
        try:
            plan_date = date_type.fromisoformat(route.plan_date)
        except ValueError:
            return None, None, route.plan_scope, route.plan_section
        return None, plan_date, route.plan_scope, route.plan_section

    # For ambiguous contextual references, ask the model only through the router's
    # clarification path; the database handler never guesses a date/day.
    return None, None, route.plan_scope, route.plan_section


async def _handle_general_qa(
    db: AsyncSession, user: User, phone: str, text: str,
    history: list[dict], summary: str | None,
) -> bool:
    """Production conversation orchestrator: classify -> validate -> execute -> answer.

    Returns True when a saved plan was fetched from DB (saved_plan_retrieval path) so
    the caller knows NOT to call _continue_plan_flow — that would deliver the same plan
    a second time. Returns False in every other case.
    """
    from app.llm.gemini_client import classify_conversation

    try:
        route = await classify_conversation(
            profile=user.profile_dict(),
            history=history,
            user_message=text,
            summary=summary,
        )
        from app.llm.route_policy import normalize_conversation_route
        route = normalize_conversation_route(route)
    except Exception:
        logger.exception("conversation_router_failed", user_id=user.id)
        route = None

    if route is None:
        # Fail open to ordinary grounded conversation, but still use the conservative
        # response-context boundary so a router outage cannot dump the full profile/history.
        response_profile, response_history, response_summary = build_response_context(
            user.profile_dict(), history, summary, None
        )
        reply, _, _ = await run_general_qa(
            response_profile, response_history, text, response_summary, grounding_required=True
        )
        db.add(Message(user_id=user.id, role="assistant", content=reply))
        await db.commit()
        await send_text_message(phone, reply)
        return

    # Read-only profile recall is deterministic after semantic classification.
    if route.intent == "profile_recall" and route.confidence >= settings.conversation_route_min_confidence:
        fields = list(route.profile_fields)
        if fields:
            reply = _format_profile_recall(user, fields)
            db.add(Message(user_id=user.id, role="assistant", content=reply))
            await db.commit()
            await send_text_message(phone, reply)
            return

    # Explicit saved-plan retrieval is also deterministic. The LLM only resolves
    # language into a plan reference; it never fabricates or edits stored content.
    if route.intent == "saved_plan_retrieval" and route.confidence >= settings.conversation_route_min_confidence:
        from app.services.diet_plan_service import get_historical_plan
        day_number, plan_date, scope, section = _resolve_plan_request(route, history)
        plan = None
        if day_number is not None:
            plan = await get_historical_plan(db, user.id, day_number=day_number)
        elif plan_date is not None:
            plan = await get_historical_plan(db, user.id, local_date=plan_date)

        if plan and plan.content:
            if scope == "section" and section:
                # Keep the stored plan immutable; ask no LLM to regenerate it.
                # A lightweight exact-section extractor is used only for known headings.
                section_map = {
                    "breakfast": "*Breakfast:*",
                    "mid_morning_snack": "*Breakfast:*",
                    "mid morning snack": "*Breakfast:*",
                    "lunch": "*Lunch:*",
                    "evening_snack": "*Evening snack:*",
                    "evening snack": "*Evening snack:*",
                    "dinner": "*Dinner:*",
                    "exercise": "*Exercise:*",
                    "hydration": "*Hydration & routine:*",
                }
                heading = section_map.get(section.casefold().strip())
                if heading and heading in plan.content:
                    # Extract the selected labeled section without changing stored data.
                    after = plan.content.split(heading, 1)[1].lstrip()
                    next_marker = re.search(r"\n\n(?:🍎|🍛|☕|🥗|🏃|💧|⚠️|🍳)\s*\*[^*]+\*:", after)
                    body = after[:next_marker.start()] if next_marker else after
                    reply = f"From your saved *Day {plan.day_number}* plan:\n\n{heading} {body.strip()}"
                else:
                    reply = f"Here is your saved *Day {plan.day_number}* diet plan. 🌿\n\n{plan.content}"
            else:
                reply = f"Here is your saved *Day {plan.day_number}* diet plan. 🌿\n\n{plan.content}"
        elif day_number is not None:
            reply = f"I don't have a saved Day {day_number} diet plan yet."
        else:
            reply = "I don't have a saved diet plan for that date yet."

        db.add(Message(user_id=user.id, role="assistant", content=reply))
        await db.commit()
        await send_text_message(phone, reply)
        return True   # plan came from DB — caller must skip _continue_plan_flow

    # Persist only semantically explicit, high-confidence updates from the current turn.
    previous_dislikes = _food_dislike_items(user.food_dislikes)
    trusted_updates = _trusted_profile_updates(route)
    if trusted_updates:
        _apply_extracted_fields(user, trusted_updates)
        await db.commit()

    new_dislikes = _food_dislike_items(user.food_dislikes) - previous_dislikes

    # Same-day plan modification is a domain side effect. Only do it after the
    # semantic router explicitly classifies the request as a plan modification
    # AND actually extracted an explicit instruction. FIX: previously this
    # branch also fired when plan_modification had no instruction, and sent
    # route.clarification_question -- an ungoverned free-text field the router
    # fills with no context-consistency guidance -- straight to the user with
    # no further check. In production this produced replies that flatly denied
    # an action the assistant's own immediately preceding message confirmed it
    # had just taken. A message with no real instruction (a question about, or
    # pushback on, a previous plan update) now simply falls through to the
    # ordinary conversational path below, which has real history/profile
    # context and dialogue_act awareness instead of a single unguided guess.
    if (
        route.intent == "plan_modification"
        and route.confidence >= settings.conversation_route_min_confidence
        and route.modification_instruction
    ):
        from app.services.diet_plan_service import revise_today_plan, _send_plan

        instruction = route.modification_instruction
        if new_dislikes:
            instruction = (
                f"{instruction} Additionally, the user explicitly added these food dislikes: "
                f"{', '.join(sorted(new_dislikes))}. Do not include them in today's plan."
            )

        revision = None
        try:
            revision = await revise_today_plan(db, user, instruction)
        except Exception:
            logger.exception(
                "today_plan_revision_failed",
                user_id=user.id,
                reason="semantic_plan_modification",
            )

        if revision and revision.plan:
            if revision.is_duplicate:
                # FIX: the same/near-same instruction was already applied a
                # short while ago (see plan_revision_policy.py). Say so
                # honestly instead of claiming a fresh update just happened,
                # and reuse the existing plan instead of regenerating another
                # different-but-equivalent version.
                reply = (
                    "Looks like I already updated today's plan for that a little while ago — "
                    "here's the current version. 🌿"
                )
            else:
                reply = "Done ✅ I updated today's saved plan based on your latest request. The updated plan is below. 🌿"
            db.add(Message(user_id=user.id, role="assistant", content=reply))
            await db.commit()
            await send_text_message(phone, reply)
            await _send_plan(user, revision.plan)
            return

        reply = (
            "I noted your request, but I couldn't update today's saved plan right now. "
            "Your preference is saved and will be respected in future plans."
        )
        db.add(Message(user_id=user.id, role="assistant", content=reply))
        await db.commit()
        await send_text_message(phone, reply)
        return
    # Everything else is ordinary conversational generation. The semantic route
    # chooses the minimum relevant state/history for the final answer; the full
    # profile and raw rolling window are never blindly injected.
    response_profile, response_history, response_summary = build_response_context(
        user.profile_dict(), history, summary, route
    )
    reply, _, _ = await run_general_qa(
        response_profile,
        response_history,
        text,
        response_summary,
        grounding_required=bool(route.grounding_required),
        allow_profile_update_tool=False,
        dialogue_act=getattr(route, "dialogue_act", "request"),
    )
    db.add(Message(user_id=user.id, role="assistant", content=reply))
    await db.commit()
    await send_text_message(phone, reply)
    return False

