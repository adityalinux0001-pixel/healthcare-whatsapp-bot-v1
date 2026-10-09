from sqlalchemy import select
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import re
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Message
from app.config import settings
from app.services.subscription_service import get_paid_subscription, prompt_payment
from app.llm.gemini_client import run_general_qa, update_conversation_summary
from app.llm.context import build_response_context
from app.knowledge.safety import (
    consent_request_message, consent_declined_message, detect_red_flag, emergency_response,
    detect_hair_concern, hair_concern_response,
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
    # Never pass the optional sexual-activity question/answer into ordinary model
    # context or long-term summary. The full message remains in the user's own
    # history for data retention; its content is redacted only at the LLM boundary.
    history: list[dict] = []
    redact_sensitive_answer = False
    for message in rows:
        content = message.content or ""
        if message.role == "assistant" and "currently sexually active" in content.casefold():
            history.append({"role": message.role, "content": "[Optional sensitive profile question omitted from AI context.]"})
            redact_sensitive_answer = True
            continue
        if redact_sensitive_answer and message.role == "user":
            history.append({"role": message.role, "content": "[Optional sensitive profile answer omitted from AI context.]"})
            redact_sensitive_answer = False
            continue
        redact_sensitive_answer = False
        history.append({"role": message.role, "content": content})
    return history


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
        if user.hair_onboarding_complete:
            subscription = await get_paid_subscription(db, user)
            if not subscription:
                await prompt_payment(db, user)
                logger.info(
                    "conversation_payment_required",
                    user_id=user.id,
                    phone=mask_identifier(phone),
                )
                return

            # During the temporary pre-launch hold, only onboarding, payment, and
            # emergency triage are active. Do not route paid users' messages to
            # Gemini, RAG, acknowledgements, check-ins, or plan-resume logic.
            # Store a redacted placeholder with the WhatsApp message ID so webhook
            # retries remain idempotent without retaining unhandled health questions.
            if settings.hair_care_launch_hold:
                from app.services.launch_mode import HAIR_CARE_LAUNCH_HOLD_STANDBY_REPLY
                db.add(Message(
                    user_id=user.id,
                    role="user",
                    content="[Message received during pre-launch hold; not processed]",
                    whatsapp_message_id=wa_message_id,
                ))
                db.add(Message(user_id=user.id, role="assistant", content=HAIR_CARE_LAUNCH_HOLD_STANDBY_REPLY))
                await db.commit()
                await send_text_message(phone, HAIR_CARE_LAUNCH_HOLD_STANDBY_REPLY)
                logger.info("hair_care_launch_hold_message_not_processed", user_id=user.id)
                return

            # Once the service is launched, route high-concern symptoms to the
            # deterministic safety message before any model-generated answer.
            hair_concern = detect_hair_concern(text)
            if hair_concern:
                await send_text_message(phone, hair_concern_response(hair_concern))
                return

        stored_user_text = text
        if (
            not user.hair_onboarding_complete
            and user.age is not None and user.age >= 18
            and user.missing_fields()
            and user.missing_fields()[0] == "sexually_active"
        ):
            # The structured field retains the answer for explicit profile recall,
            # but routine chat history and LLM context never store the raw response.
            stored_user_text = "[Optional sensitive profile answer supplied]"
        db.add(Message(
            user_id=user.id,
            role="user",
            content=stored_user_text,
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
        summary = user.conversation_summary if user.hair_onboarding_complete else None

        if not user.hair_onboarding_complete:
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
        if user.hair_onboarding_complete and msg_count > 0 and msg_count % SUMMARY_EVERY_N == 0:
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
    "done": "Great, thanks! 🌿 Day {n} hair-care routine marked as tried.",
    "not_done": "No worries — every day is a fresh start. Day {n} marked as not tried.",
    "skip": "Okay, Day {n} check-in skipped. ⏭️",
}


async def _enqueue_plan_resume(user: User, *, source_id: str | int | None = None) -> None:
    if settings.hair_care_launch_hold:
        logger.info("hair_care_launch_hold_enqueue_skipped", user_id=user.id)
        return
    from app.redis_client import get_arq_pool

    pool = await get_arq_pool()
    local_date = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    job_suffix = str(source_id) if source_id is not None else "daily"
    await pool.enqueue_job(
        "generate_hair_care_plan_for_user", user.id,
        _job_id=f"hair_resume:{user.id}:{local_date}:{job_suffix}",
    )


async def _continue_plan_flow(db: AsyncSession, user: User, phone: str) -> None:
    """Resume today's hair-care routine only when the service says it is eligible."""
    if settings.hair_care_launch_hold or not user.hair_onboarding_complete:
        return
    try:
        from app.services import hair_care_plan_service as hps
        if not hps.is_routine_eligible_user(user):
            return
        if await hps.needs_plan_resume(db, user):
            await _enqueue_plan_resume(user)
    except Exception:
        logger.exception("hair_care_plan_resume_enqueue_failed", user_id=user.id)


async def _record_and_reply_checkin(
    db: AsyncSession, user: User, phone: str, plan_id: int, choice: str
) -> None:
    from app.services import hair_care_plan_service as hps

    result, plan = await hps.record_checkin(db, user, plan_id, choice)
    if result == "not_found" or plan is None:
        reply = "I couldn't match that check-in to an active hair-care routine. You can ask me for your latest routine. 🌿"
        db.add(Message(user_id=user.id, role="assistant", content=reply))
        await db.commit()
        await send_text_message(phone, reply)
        return

    if result == "already":
        reply = "✅ Already noted — thank you!"
        resume = False
    else:
        try:
            resume = await hps.needs_plan_resume(db, user)
        except Exception:
            resume = False
            logger.exception("hair_care_checkin_resume_check_failed", user_id=user.id)
        if settings.hair_care_launch_hold:
            tail = "Daily hair-care plans are temporarily being prepared for launch. We expect to begin sending them within 2–3 business days. 🌿"
            resume = False
        else:
            tail = (
                "I'll prepare today's hair-care routine now… 🌿"
                if resume
                else "Your next daily hair-care routine is scheduled for 6:00 AM IST. 🌿"
            )
        reply = f"{_CHECKIN_ACK[choice].format(n=plan.day_number)}\n\n{tail}"

    db.add(Message(user_id=user.id, role="assistant", content=reply))
    await db.commit()
    await send_text_message(phone, reply)

    if result == "recorded" and resume:
        try:
            await _enqueue_plan_resume(user, source_id=f"checkin-{plan.id}")
        except Exception:
            logger.exception("hair_care_checkin_resume_enqueue_failed", user_id=user.id, plan_id=plan.id)
    logger.info("hair_care_checkin_recorded", user_id=user.id, plan_id=plan_id, choice=choice, result=result)


async def _handle_button_reply(db: AsyncSession, user: User, phone: str, button_id: str) -> None:
    from app.services.hair_care_plan_service import parse_checkin_button_id

    parsed = parse_checkin_button_id(button_id)
    if parsed:
        plan_id, choice = parsed
        await _record_and_reply_checkin(db, user, phone, plan_id, choice)
        return

    # Old diet-plan buttons remain non-operative after product migration.
    if button_id.startswith("chk:"):
        reply = "That was an old diet-plan check-in and is no longer active. 🌿 Please use the latest hair-care routine check-in."
    else:
        reply = "I couldn't recognize that button. Please send your question as a normal message."
    db.add(Message(user_id=user.id, role="assistant", content=reply))
    await db.commit()
    await send_text_message(phone, reply)


async def _try_text_checkin_answer(db: AsyncSession, user: User, phone: str, text: str) -> bool:
    from app.services.hair_care_plan_service import parse_checkin_text, get_pending_checkin_plan

    plan = await get_pending_checkin_plan(db, user.id)
    if not plan:
        return False
    choice = parse_checkin_text(text)
    if not choice:
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
            # Sexual-activity data is not collected from minors even if a stale or
            # malformed extraction somehow supplies it.
            if field == "sexually_active" and user.age is not None and user.age < 18:
                user.sexually_active = None
                continue
            setattr(user, field, value)
            if field == "allergies":
                user.allergies_answered = True
            elif field == "medical_conditions":
                user.medical_conditions_answered = True
    if user.age is not None and user.age < 18:
        user.sexually_active = None


async def _handle_onboarding(
    db: AsyncSession, user: User, phone: str, text: str,
    history: list[dict], summary: str | None,
) -> None:
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

    just_completed = (not user.hair_onboarding_complete) and (not missing_after)
    if just_completed:
        user.hair_onboarding_complete = True
        # Keep this legacy flag true for existing admin/reporting code while the
        # dedicated flag drives Hair & Scalp routing.
        user.onboarding_complete = True
        # Do not carry a legacy weight-loss summary into the new hair assistant.
        user.conversation_summary = None
        if settings.hair_care_launch_hold:
            reply = (
                "🎉 Amazing! Your hair & scalp profile is complete.\n\n"
                f"Your personalized {settings.subscription_days}-day hair-care plan is ready to be activated, "
                "built around your hair-wash habits, water type, lifestyle and family history. 🌿\n\n"
                "Activate it with the secure link below 👇"
            )
        else:
            reply = (
                "Perfect! ✅ Your hair and scalp profile is complete.\n\n"
                "You can now ask me about hair shedding, thinning, dandruff, scalp care, and hair-care habits. "
                "I share general evidence-based information, but I can't diagnose a condition or prescribe treatment."
            )
    else:
        next_field = missing_after[0]
        reply = QUESTIONS.get(next_field, "Could you share a bit more about yourself? 😊")

    db.add(Message(user_id=user.id, role="assistant", content=reply))
    await db.commit()
    await send_text_message(phone, reply)

    # Hair profile completion unlocks the existing subscription gate. Paid adult
    # users get their first daily hair-care routine via the same idempotent worker.
    if just_completed:
        subscription = await get_paid_subscription(db, user)
        if not subscription:
            await prompt_payment(db, user)
            logger.info("hair_onboarding_complete_payment_prompted", user_id=user.id)
        else:
            logger.info("hair_onboarding_complete_paid_entitlement_found", user_id=user.id)
            from app.services.hair_care_plan_service import routine_eligibility_block_reason
            reason = routine_eligibility_block_reason(
                age=user.age,
                medical_conditions=user.medical_conditions,
                hair_onboarding_complete=user.hair_onboarding_complete,
            )
            if settings.hair_care_launch_hold:
                from app.services.launch_mode import HAIR_CARE_LAUNCH_HOLD_NOTICE, routine_unavailable_notice
                notice = HAIR_CARE_LAUNCH_HOLD_NOTICE if reason is None else routine_unavailable_notice(reason)
                db.add(Message(user_id=user.id, role="assistant", content=notice))
                await db.commit()
                await send_text_message(phone, notice)
            elif reason is None:
                try:
                    await _enqueue_plan_resume(user, source_id="onboarding")
                except Exception:
                    logger.exception("first_hair_care_plan_enqueue_failed", user_id=user.id)



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
        "city": "City",
        "gender": "Gender",
        "height_cm": "Height",
        "weight_kg": "Weight",
        "hair_wash_frequency": "Hair-washing frequency",
        "water_hardness": "Water type",
        "sugary_food_drink_intake": "Sugary food and drink intake",
        "sexually_active": "Sexual activity answer",
        "family_hair_loss": "Family history of hair loss",
        "family_hair_loss_relation": "Family member(s) with hair loss",
        "dairy_intake": "Dairy intake",
        "activity_level": "Activity level (legacy profile)",
        "goal": "Goal (legacy profile)",
        "diet_preference": "Diet preference (legacy profile)",
        "allergies": "Previously saved allergies",
        "medical_conditions": "Previously saved medical conditions",
        "food_dislikes": "Previously saved food dislikes",
    }
    values = user.profile_dict(include_sensitive=True, include_legacy=True)
    visible: list[str] = []
    for field in fields:
        value = values.get(field)
        if value in (None, ""):
            continue
        if field in {"height_cm", "weight_kg"}:
            suffix = " cm" if field == "height_cm" else " kg"
            value = f"{value}{suffix}"
        elif field in {"goal", "activity_level", "diet_preference", "hair_wash_frequency", "water_hardness", "sugary_food_drink_intake", "sexually_active", "family_hair_loss", "family_hair_loss_relation", "dairy_intake"}:
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
        # Sexual activity is sensitive and is only collected by the deterministic
        # onboarding step; normal LLM-driven updates may never write this field.
        if candidate.field == "sexually_active":
            continue
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
        from app.services.hair_care_plan_service import get_historical_plan
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
                    "morning": "*Morning:*",
                    "morning_action": "*Morning:*",
                    "wash": "*Wash & scalp care:*",
                    "wash and scalp care": "*Wash & scalp care:*",
                    "wash_and_scalp_care": "*Wash & scalp care:*",
                    "daytime": "*During the day:*",
                    "daytime_habit": "*During the day:*",
                    "nourishment": "*Everyday nourishment:*",
                    "nourishment_habit": "*Everyday nourishment:*",
                    "evening": "*Evening:*",
                    "evening_action": "*Evening:*",
                    "safety": "*Please note:*",
                    "safety_note": "*Please note:*",
                }
                heading = section_map.get(section.casefold().strip())
                if heading and heading in plan.content:
                    # Extract the selected labeled section without changing stored data.
                    after = plan.content.split(heading, 1)[1].lstrip()
                    next_marker = re.search(r"\n\n(?:☀️|🧴|🌤️|🥗|🌙|ℹ️)\s*\*[^*]+\*:", after)
                    body = after[:next_marker.start()] if next_marker else after
                    reply = f"From your saved *Day {plan.day_number}* hair-care routine:\n\n{heading} {body.strip()}"
                else:
                    reply = f"Here is your saved *Day {plan.day_number}* hair-care routine. 🌿\n\n{plan.content}"
            else:
                reply = f"Here is your saved *Day {plan.day_number}* hair-care routine. 🌿\n\n{plan.content}"
        elif day_number is not None:
            reply = f"I don't have a saved Day {day_number} hair-care routine yet."
        else:
            reply = "I don't have a saved hair-care routine for that date yet."

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
        from app.services.hair_care_plan_service import revise_today_plan, _send_plan

        instruction = route.modification_instruction

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
                # Idempotent duplicate modification: return the saved content
                # in this reply. Do not call _send_plan(), which correctly refuses
                # to redeliver an already-sent plan.
                reply = (
                    "I already applied that change to today's hair-care routine, so I kept the saved version unchanged. 🌿\n\n"
                    f"{revision.plan.content}"
                )
                db.add(Message(user_id=user.id, role="assistant", content=reply))
                await db.commit()
                await send_text_message(phone, reply)
                return

            reply = "Done ✅ I updated today's saved hair-care routine. The updated version is below. 🌿"
            db.add(Message(user_id=user.id, role="assistant", content=reply))
            await db.commit()
            await send_text_message(phone, reply)
            await _send_plan(user.id, revision.plan.id)
            return

        reply = (
            "I couldn't update today's saved hair-care routine right now. Please try again, or ask me for general hair-care guidance."
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