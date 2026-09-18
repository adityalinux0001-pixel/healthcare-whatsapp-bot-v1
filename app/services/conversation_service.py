from sqlalchemy import select
from datetime import date
import re
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Message
from app.config import settings
from app.services.subscription_service import get_active_subscription, prompt_payment
from app.llm.gemini_client import run_general_qa, update_conversation_summary
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


async def handle_incoming_message(
    db: AsyncSession, phone: str, text: str, wa_message_id: str
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
            await send_text_message(phone, emergency_response())
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

        history = await _recent_history(db, user, exclude_whatsapp_message_id=wa_message_id)
        summary = user.conversation_summary

        if not user.onboarding_complete:
            await _handle_onboarding(db, user, phone, text, history, summary)
        else:
            handled_ack = await _send_simple_acknowledgement(db, user, phone, text)
            if not handled_ack:
                await _handle_general_qa(db, user, phone, text, history, summary)

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
    """Fully deterministic, LLM-free onboarding state machine.

    One required field is targeted at a time (the first entry of
    ``user.missing_fields()``, which is always in the same fixed order — see
    app/services/onboarding_extract.ONBOARDING_ORDER). Every turn:

      1. Parse the message with extract_fields_from_text(). The current
         target field gets permissive parsing (bare numbers/words accepted);
         every other still-missing field only matches unambiguous phrasing,
         so a stray word never gets filed under the wrong question.
      2. Apply whatever validated fields came out of that.
      3. If the CURRENT target is still unanswered, re-send the exact same
         question plus a short format hint and stop — we never advance,
         never guess, and never silently drop the field. This is what
         prevents both failure modes seen before: names getting mangled by
         free-form LLM extraction, and the allergies/medical-conditions
         question looping because a "no" variant didn't match a strict
         fullmatch regex.
      4. Otherwise move on to the next missing field, or finish onboarding.

    No Gemini call happens anywhere in this path, so there is nothing here
    that can behave differently between two identical inputs.
    """
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

    # Personalized plans are adult-only. Keep the conversation open so the user
    # can correct an accidentally extracted age later, but never enqueue a plan
    # for a minor.
    if user.age is not None and user.age < 18:
        reply = (
            "This personalized diet and exercise service is available for adults aged 18 and over. "
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


def _today_plan_change_request(
    text: str,
    history: list[dict],
    new_dislikes: set[str],
) -> tuple[str | None, str | None]:
    """Detect an explicit same-day plan change without affecting ordinary Q&A."""
    normalized = re.sub(r"\s+", " ", text.casefold()).strip()

    if new_dislikes:
        foods = ", ".join(sorted(new_dislikes))
        return (
            "food_dislike",
            f"User explicitly added these foods to their dislikes: {foods}. "
            "Regenerate today's existing plan so these foods do not appear in meal ingredients. "
            "Preserve all allergies, diet preference, medical safety, goal, and other constraints.",
        )

    fast_words = r"\b(?:fast|fasting)\b"
    today_words = r"\b(?:today)\b"
    fasting_food_details = r"\b(?:allowed|allow|not allowed|avoid|only|fruits?|dairy|milk|sabudana|sago|nuts?|dry fruits?)\b"

    if re.search(fast_words, normalized) and re.search(today_words, normalized):
        if re.search(fasting_food_details, normalized):
            return (
                "fast",
                "User says they are fasting today and explicitly provided fasting food permissions/restrictions "
                f"in the current message: {text.strip()}. Use ONLY what the user explicitly stated. "
                "Do not invent or assume religion-specific fasting rules. Preserve all other profile and safety constraints.",
            )
        return "fast_needs_details", None

    # The user may answer the bot's fasting clarification on the next turn with
    # only the allowed foods. Recent user history supplies the fasting context.
    recent_user_text = " ".join(
        turn.get("content", "") for turn in history[-4:] if turn.get("role") == "user"
    ).casefold()
    if re.search(fast_words, recent_user_text) and re.search(fasting_food_details, normalized):
        return (
            "fast",
            "The user previously said they are fasting today and has now provided fasting food permissions/restrictions. "
            f"Use ONLY the current user's stated fasting details: {text.strip()}. "
            "Do not invent or assume religion-specific fasting rules. Preserve all other profile and safety constraints.",
        )

    return None, None


async def _handle_general_qa(
    db: AsyncSession, user: User, phone: str, text: str,
    history: list[dict], summary: str | None,
) -> None:
    previous_dislikes = _food_dislike_items(user.food_dislikes)
    reply, extracted, plan_request = await run_general_qa(
        user.profile_dict(), history, text, summary
    )
    _apply_extracted_fields(user, extracted)
    new_dislikes = _food_dislike_items(user.food_dislikes) - previous_dislikes

    # Historical-plan retrieval always wins. A request for an old plan must never
    # be converted into a current-day revision.
    change_kind, modification_instruction = (None, None)
    if plan_request is None:
        change_kind, modification_instruction = _today_plan_change_request(
            text, history, new_dislikes
        )

    # Historical diet plans are factual database records. Never ask Gemini to
    # recreate them; retrieve the exact stored content by immutable day/date.
    revised_plan = None
    if change_kind == "fast_needs_details":
        reply = (
            "Understood — you are fasting today. 🙏 Which foods are allowed during your fast "
            "(for example, fruits, dairy, sabudana, nuts, etc.)? Please tell me, and I will update "
            "today's plan accordingly."
        )
    elif modification_instruction:
        from app.services.diet_plan_service import revise_today_plan, _send_plan
        try:
            revised_plan = await revise_today_plan(db, user, modification_instruction)
        except Exception:
            logger.exception(
                "today_plan_revision_failed",
                user_id=user.id,
                change_kind=change_kind,
            )
            revised_plan = None

        if revised_plan:
            reply = "Done ✅ Today's diet plan has been updated according to your latest preference. The new plan is below. 🌿"
        else:
            reply = (
                "I have noted your preference. Today's saved plan could not be updated right now, "
                "but your preference will be followed in future plans."
            )

    if plan_request:
        from app.services.diet_plan_service import get_historical_plan

        plan = None
        day_number = plan_request.get("day_number")
        if day_number is not None:
            try:
                day_number = int(day_number)
                if day_number >= 1:
                    plan = await get_historical_plan(
                        db, user.id, day_number=day_number
                    )
            except (TypeError, ValueError):
                plan = None

        if plan is None and plan_request.get("plan_date"):
            try:
                requested_date = date.fromisoformat(str(plan_request["plan_date"]))
                plan = await get_historical_plan(
                    db, user.id, local_date=requested_date
                )
            except ValueError:
                plan = None

        if plan and plan.content:
            reply = (
                f"Here is your saved *Day {plan.day_number}* diet plan. 🌿\n\n"
                f"{plan.content}"
            )
        else:
            requested = (
                f"Day {day_number}" if day_number else "us date ka"
            )
            reply = (
                f"I could not find a saved diet plan for {requested}. "
                "I can show you your current plan instead. 😊"
            )

    db.add(Message(user_id=user.id, role="assistant", content=reply))
    await db.commit()
    await send_text_message(phone, reply)
    if revised_plan is not None:
        await _send_plan(user, revised_plan)