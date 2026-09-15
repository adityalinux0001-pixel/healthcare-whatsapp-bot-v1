from sqlalchemy import select
from datetime import date
import re
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User, Message
from app.config import settings
from app.services.subscription_service import get_active_subscription, prompt_payment
from app.llm.gemini_client import run_onboarding_turn, run_general_qa, update_conversation_summary
from app.knowledge.safety import consent_request_message, consent_declined_message, detect_red_flag, emergency_response
from app.whatsapp.client import send_text_message
from app.redis_client import redis_client
from app.utils.logging_config import logger
from app.utils.security import mask_identifier
from app.services.profile_validation import validate_extracted_fields

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
                reply = (
                    "Thank you. ✅ Consent recorded.\n\n"
                    "Let's complete your profile first. Please tell me your name 😊"
                )
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



def _deterministic_health_answers(text: str, missing_fields: list[str]) -> dict[str, str]:
    """Capture explicit no/none health answers even when Gemini skips the tool call.

    Health completion is state-critical: an explicit "I don't have any allergies and
    medical conditions" must close both onboarding requirements deterministically.
    """
    normalized = re.sub(r"\s+", " ", text.casefold()).strip()
    result: dict[str, str] = {}

    no_prefix = r"(?:no|none|don't have|dont have|do not have|without|never had)"
    allergy_term = r"(?:allerg(?:y|ies)|allergen(?:s)?)"
    medical_term = r"(?:medical (?:condition|conditions)|health condition(?:s)?|disease(?:s)?)"

    if "allergies" in missing_fields:
        allergy_no = re.search(
            rf"\b{no_prefix}\b[^.?!;]*\b{allergy_term}\b|\b{allergy_term}\b[^.?!;]*\b{no_prefix}\b",
            normalized,
        )
        if allergy_no or normalized in {"no allergies", "none"}:
            result["allergies"] = "None reported"

    if "medical_conditions" in missing_fields:
        medical_no = re.search(
            rf"\b{no_prefix}\b[^.?!;]*\b{medical_term}\b|\b{medical_term}\b[^.?!;]*\b{no_prefix}\b",
            normalized,
        )
        if medical_no or normalized in {"no medical conditions", "no medical condition", "none"}:
            result["medical_conditions"] = "None reported"

    # Common combined answer, including the exact phrase used in testing.
    if re.search(r"\b(no|none|don't have|dont have|do not have)\b", normalized):
        if "allergies" in missing_fields and re.search(r"allerg", normalized):
            result["allergies"] = "None reported"
        if "medical_conditions" in missing_fields and re.search(r"medical\s+(?:condition|conditions)", normalized):
            result["medical_conditions"] = "None reported"

    return result

def _deterministic_profile_hints(
    text: str,
    history: list[dict] | None = None,
) -> dict[str, object]:
    """Extract only high-confidence profile facts from natural-language onboarding text.

    This is intentionally conservative. Gemini remains the primary extractor, while this
    layer catches common compact/messy formats and context-dependent answers so onboarding
    cannot loop or silently lose explicitly stated profile facts.
    """
    normalized = re.sub(r"\s+", " ", text.casefold()).strip()
    result: dict[str, object] = {}

    # Name: only accept when introduced as a name/self-identification and followed by a
    # clear profile token or end of sentence. This prevents phrases like "I am vegetarian"
    # from becoming the user's name.
    name_match = re.search(
        r"\b(?:my name is|name is|i am|i'm|this is)\s+"
        r"([a-z][a-z.'-]*(?:\s+[a-z][a-z.'-]*){0,4})"
        r"(?=\s+(?:male|female|man|woman|m|f)\b|\s+\d{1,3}\s*(?:years?|yrs?|yo)\b|"
        r"\s+\d{3}(?:\.\d+)?\s*cm\b|\s+\d{2,3}(?:\.\d+)?\s*kg\b|"
        r"\s+(?:vegetarian|vegeterian|vegitarian|veg|vegan|non[- ]?veg|eggetarian|eggitarian)\b|"
        r"\s+(?:desk job|office job|work from home)\b|\s+(?:and|,|;)|$)",
        normalized,
    )
    if name_match:
        candidate = name_match.group(1).strip(" ,;.-")
        # Do not accept obvious non-name phrases.
        blocked = {
            "vegetarian", "vegeterian", "vegitarian", "veg", "vegan",
            "male", "female", "man", "woman", "none", "no",
            "very active", "active", "lightly active", "moderately active",
            "looking to", "trying to", "going to", "not sure",
        }
        candidate_words = set(candidate.split())
        non_name_words = {
            "very", "active", "lightly", "moderately", "looking", "trying",
            "want", "wants", "gain", "build", "lose", "maintain", "have",
            "do", "work", "working", "from", "with", "at", "for", "and",
        }
        if (
            candidate
            and candidate not in blocked
            and not candidate_words.intersection(non_name_words)
        ):
            result["name"] = candidate.title()

    # Gender. Single-letter forms are accepted only as standalone tokens.
    gender_match = re.search(r"\b(male|female|man|woman|m|f)\b", normalized)
    if gender_match:
        result["gender"] = {
            "male": "male", "man": "male", "m": "male",
            "female": "female", "woman": "female", "f": "female",
        }[gender_match.group(1)]

    # Age: prefer explicit age wording, then a number immediately after gender.
    age_match = re.search(r"\bage\s*(?:is|:|-)?\s*(\d{1,3})\b", normalized)
    if not age_match:
        age_match = re.search(
            r"\b(?:male|female|man|woman)\s*,?\s*(\d{1,3})\s*(?:years?|yrs?|yo)?\b",
            normalized,
        )
    if not age_match:
        age_match = re.search(r"\b(\d{1,3})\s*(?:years?|yrs?|yo)\b", normalized)
    if age_match:
        age = int(age_match.group(1))
        if 1 <= age <= 120:
            result["age"] = age

    height_match = re.search(
        r"\b(\d{3}(?:\.\d+)?)\s*(?:cm|cms|centimeters?|centimetres?)\b",
        normalized,
    )
    if height_match:
        height = float(height_match.group(1))
        if 100 <= height <= 250:
            result["height_cm"] = height

    weight_match = re.search(
        r"\b(\d{2,3}(?:\.\d+)?)\s*(?:kg|kgs|kilograms?)\b",
        normalized,
    )
    if weight_match:
        weight = float(weight_match.group(1))
        if 20 <= weight <= 350:
            result["weight_kg"] = weight

    # Diet preference. Explicit terms win; never map vegetarian to vegan.
    diet_patterns = [
        (r"\b(?:non[- ]?veg|non[- ]?vegetarian|nonvegetarian)\b", "non_veg"),
        (r"\b(?:eggetarian|eggitarian)\b", "eggetarian"),
        (r"\bvegan\b", "vegan"),
        (r"\b(?:vegetarian|vegeterian|vegitarian|veg)\b", "veg"),
    ]
    for pattern, value in diet_patterns:
        if re.search(pattern, normalized):
            result["diet_preference"] = value
            break

    # Goal. Use only common unambiguous phrases.
    goal_patterns = [
        (r"\b(?:gain muscle|gain muscles|build muscle|build muscles|put on muscle|muscle gain)\b", "muscle_gain"),
        (r"\b(?:lose weight|lose fat|fat loss|weight loss)\b", "weight_loss"),
        (r"\b(?:gain weight|put on weight|weight gain)\b", "weight_gain"),
        (r"\bmaintain(?: weight)?\b", "maintain"),
    ]
    for pattern, value in goal_patterns:
        if re.search(pattern, normalized):
            result["goal"] = value
            break

    # Explicit activity labels.
    activity_patterns = [
        (r"\b(?:sedentary|inactive|not active|sedentry)\b", "sedentary"),
        (r"\b(?:light(?:ly)? active)\b", "light"),
        (r"\b(?:moderate(?:ly)? active)\b", "moderate"),
        (r"\b(?:very active|active)\b", "active"),
    ]
    for pattern, value in activity_patterns:
        if re.search(pattern, normalized):
            result["activity_level"] = value
            break

    # A desk job is a useful low-activity signal. Only default to sedentary when the
    # same message does not clearly describe regular exercise/physical activity.
    desk_job = bool(re.search(r"\b(?:desk job|office job|work from home|wfh)\b", normalized))
    regular_activity = bool(re.search(
        r"\b(?:gym|workout|work out|exercise|running|jogging|cycling|sports?|training|walk(?:ing)?\s+daily|"
        r"daily\s+(?:walk|exercise|workout)|regular(?:ly)?\s+(?:exercise|workout|train))\b",
        normalized,
    ))
    negative_activity = bool(re.search(
        r"\b(?:no|not|don't|dont|do not|never)\s+(?:exercise|workout|work out|gym|sports?|physical activity)\b",
        normalized,
    ))
    if desk_job and (not regular_activity or negative_activity):
        result["activity_level"] = "sedentary"

    # Contextual one-word/no answer: if the assistant's recent question was clearly about
    # exercise/activity and the user answers "no", classify the answer in that context only.
    if normalized in {"no", "nope", "nah", "none", "not really", "i don't", "i dont"}:
        recent_assistant = " ".join(
            turn.get("content", "") for turn in (history or [])[-3:] if turn.get("role") == "assistant"
        ).casefold()
        if re.search(r"\b(?:activity|active|exercise|workout|work out|gym|physical activity)\b", recent_assistant):
            result["activity_level"] = "sedentary"

    return result

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

    profile = user.profile_dict()
    missing = user.missing_fields()

    # LLM remains the natural-language extractor, but high-confidence deterministic
    # hints protect state-critical onboarding from spelling/format noise and context-only
    # answers such as a standalone "no" after an exercise question.
    deterministic_health = _deterministic_health_answers(text, missing)
    deterministic_profile = _deterministic_profile_hints(text, history)
    deterministic = {**deterministic_profile, **deterministic_health}

    reply, extracted = await run_onboarding_turn(
        profile, missing, history, text, summary, explicit_fields=deterministic
    )
    if deterministic:
        extracted = {**extracted, **deterministic}
        logger.info(
            "deterministic_onboarding_fields_extracted",
            fields=sorted(deterministic),
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

    just_completed = (not user.onboarding_complete) and (not user.missing_fields())
    if just_completed:
        user.onboarding_complete = True
        reply = (
            f"Perfect, {user.name or 'there'}! ✅ Your profile is complete.\n\n"
            "Your personalized daily diet and exercise plan is now being generated based on "
            "your goals and preferences. 🌿"
        )

    db.add(Message(user_id=user.id, role="assistant", content=reply))
    await db.commit()
    await send_text_message(phone, reply)

    # Ask for payment only after onboarding is fully complete. If the user already
    # has an active subscription, preserve the existing immediate plan generation path.
    if just_completed:
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
