"""Gemini client and grounded generation helpers."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.knowledge.retrieval import retrieve_general_context
from app.knowledge.safety import detect_red_flag, emergency_response, detect_hair_concern, hair_concern_response
from app.knowledge.store import KnowledgeBaseNotReady
from app.llm.conversation_schemas import ConversationRoute
from app.llm.prompts import (
    GENERAL_QA_SYSTEM_PROMPT,
    HAIR_CARE_PLAN_PROMPT,
    HAIR_CARE_PLAN_REVISION_PROMPT,
    SUMMARY_UPDATE_PROMPT,
    UPDATE_PROFILE_FUNCTION,
    CONVERSATION_ROUTER_PROMPT,
)
from app.llm.schemas import HairCarePlanOutput


class PlanGenerationValidationError(RuntimeError):
    """The model returned a structurally valid plan that failed app safety constraints."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("Generated plan failed deterministic safety/constraint validation.")


client = genai.Client(api_key=settings.gemini_api_key)
_update_profile_tool = types.Tool(function_declarations=[UPDATE_PROFILE_FUNCTION])


def _history_to_contents(history: list[dict]) -> list[types.Content]:
    contents = []
    for turn in history:
        role = "model" if turn["role"] == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=turn["content"])]))
    return contents


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8))
async def _generate(model: str, contents, config) -> types.GenerateContentResponse:
    return await client.aio.models.generate_content(model=model, contents=contents, config=config)


def _extract_function_calls(response: types.GenerateContentResponse):
    extracted: dict = {}
    tool_calls: list[tuple[str, dict, str | None]] = []
    candidate = response.candidates[0]
    reply_text = ""
    for part in candidate.content.parts:
        fn = getattr(part, "function_call", None)
        if fn:
            args = {k: v for k, v in fn.args.items() if v not in (None, "")}
            name = getattr(fn, "name", "")
            tool_calls.append((name, args, getattr(fn, "id", None)))
            extracted.update(args)
        if getattr(part, "text", None):
            reply_text += part.text
    return extracted, None, candidate.content, reply_text, tool_calls


async def _tool_followup(contents, candidate_content, tool_calls, system_prompt: str) -> str:
    response_parts = []
    for name, args, call_id in tool_calls:
        kwargs = {
            "name": name,
            "response": {"accepted_fields": args, "status": "accepted_for_application_persistence"},
        }
        if call_id:
            kwargs["id"] = call_id
        response_parts.append(types.Part.from_function_response(**kwargs))
    next_contents = list(contents)
    next_contents.append(candidate_content)
    if response_parts:
        next_contents.append(types.Content(role="user", parts=response_parts))
    else:
        next_contents.append(types.Content(role="user", parts=[types.Part(text="Now provide the final natural conversational reply in English.")]))
    followup = await _generate(
        settings.gemini_model_chat,
        next_contents,
        types.GenerateContentConfig(system_instruction=system_prompt),
    )
    return followup.text or "Understood. I have noted that."


async def run_general_qa(profile, history, user_message, summary=None, allow_profile_update_tool=False, grounding_required=True, dialogue_act="request"):
    red_flag = detect_red_flag(user_message)
    if red_flag:
        return emergency_response(), {}, None
    hair_concern = detect_hair_concern(user_message)
    if hair_concern:
        return hair_concern_response(hair_concern), {}, None

    if grounding_required:
        try:
            knowledge_context = await retrieve_general_context(profile, user_message, top_k=settings.knowledge_top_k_qa)
        except KnowledgeBaseNotReady:
            return (
                "I do not have enough verified health knowledge context to answer safely right now, so I will not guess. "
                "Please try again later.",
                {},
                None,
            )
    else:
        knowledge_context = "Not required for this conversational turn."

    system_prompt = GENERAL_QA_SYSTEM_PROMPT.format(
        today_date=datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),
        profile=profile,
        summary=summary or "No summary yet.",
        knowledge_context=knowledge_context,
    ) + (
        "\n\nCURRENT DIALOGUE ACT: " + str(dialogue_act) +
        "\nTreat this as authoritative application metadata. If it is 'request', answer only the current request. "
        "If it is 'follow_up', use only the selected preceding context to resolve the follow-up. "
        "If it is 'accept_offer', directly fulfill the specific offer from the immediately preceding assistant turn. "
    )
    contents = _history_to_contents(history)
    contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))
    # Historical plan retrieval is intentionally NOT an LLM tool. Explicit saved-plan
    # requests are routed deterministically by conversation_service before Gemini is called.
    tools = [_update_profile_tool] if allow_profile_update_tool else []
    response = await _generate(
        settings.gemini_model_chat,
        contents,
        types.GenerateContentConfig(
            system_instruction=system_prompt,
            **({"tools": tools} if tools else {}),
            **({"automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True)} if tools else {}),
        ),
    )
    extracted, _plan_request_unused, candidate_content, reply_text, tool_calls = _extract_function_calls(response)

    if tool_calls and extracted and not reply_text and candidate_content:
        update_calls = [call for call in tool_calls if call[0] == "update_profile"]
        reply_text = await _tool_followup(contents, candidate_content, update_calls, system_prompt)
    elif not reply_text and candidate_content:
        reply_text = await _tool_followup(contents, candidate_content, [], system_prompt)

    return reply_text or "Sorry, I could not understand that. Could you please rephrase?", extracted, None



_HAIR_PLAN_UNSAFE_PATTERNS = (
    # Medication/treatment instructions are out of scope for this non-medical routine.
    r"\b(?:minoxidil|finasteride|dutasteride|spironolactone|isotretinoin|ketoconazole|clobetasol)\b",
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|iu)\b",
    r"\b(?:take|start|increase|double|recommend|use)\s+(?:a\s+)?(?:supplement|biotin|iron|zinc|vitamin|collagen)\b",
    r"\b(?:hair[- ]growth|regrowth|anti[- ]hair[- ]loss)\s+(?:serum|product|treatment|medication)\b",
    r"\b(?:blood test|ferritin test|thyroid test|lab test|laboratory test)\b",
    r"\b(?:guaranteed to|guarantee(?:d)? regrowth|cure hair loss|will definitely regrow)\b",
)


def _hair_plan_validation_problems(plan: HairCarePlanOutput) -> list[str]:
    import re

    text = "\n".join(getattr(plan, field) for field in (
        "morning_action", "wash_and_scalp_care", "daytime_habit",
        "nourishment_habit", "evening_action", "safety_note",
    ))
    problems: list[str] = []
    if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _HAIR_PLAN_UNSAFE_PATTERNS):
        problems.append("The routine contains medication, treatment, lab-test, supplement, dosage, or guaranteed-result language.")
    safety_note = plan.safety_note.casefold()
    if not any(phrase in safety_note for phrase in (
        "general care", "general hair care", "general self-care", "general information", "general routine"
    )):
        problems.append("The safety note must explain that this is general care.")
    has_no_diagnosis = any(phrase in safety_note for phrase in (
        "not a diagnosis", "not diagnosis", "not intended to diagnose", "does not diagnose", "no diagnosis"
    ))
    has_no_treatment = any(phrase in safety_note for phrase in (
        "not a treatment", "not treatment", "not medical treatment", "not intended to treat",
        "not a diagnosis or treatment", "not a diagnosis or medical treatment"
    ))
    if not (has_no_diagnosis and has_no_treatment):
        problems.append("The safety note must state that the routine is neither a diagnosis nor treatment.")
    if not any(phrase in safety_note for phrase in (
        "dermatologist", "qualified clinician", "healthcare professional", "health-care professional", "doctor"
    )):
        problems.append("The safety note must say when to seek professional assessment.")
    if len(text) > 2100:
        problems.append("Routine is too long for a concise WhatsApp message.")
    return problems


def _render_hair_care_plan(plan: HairCarePlanOutput, day_number: int) -> str:
    content = (
        f"🌿 *Your Daily Hair-Care Routine — Day {day_number}*\n\n"
        f"☀️ *Morning:* {plan.morning_action}\n\n"
        f"🧴 *Wash & scalp care:* {plan.wash_and_scalp_care}\n\n"
        f"🌤️ *During the day:* {plan.daytime_habit}\n\n"
        f"🥗 *Everyday nourishment:* {plan.nourishment_habit}\n\n"
        f"🌙 *Evening:* {plan.evening_action}\n\n"
        f"ℹ️ *Please note:* {plan.safety_note}"
    )
    if len(content) > 3600:
        raise PlanGenerationValidationError(["Rendered routine exceeds WhatsApp-safe length."])
    return content


async def generate_hair_care_plan(
    profile, recent_plans, summary=None, day_number=1,
    modification_instruction: str | None = None,
    current_plan_text: str | None = None,
) -> str:
    """Generate a grounded, non-medical daily hair-care routine with deterministic checks."""
    plan_question = (
        "Create a simple daily hair-care routine based on authoritative dermatologist guidance: "
        "gentle washing and conditioning, scalp/hair handling, avoiding damage from heat/chemical processing "
        "and tight hairstyles, general balanced nourishment, and when to seek a dermatologist. "
        "Do not diagnose or recommend medicine or supplements."
    )
    knowledge_context = await retrieve_general_context(
        profile, plan_question, top_k=settings.knowledge_top_k_qa
    )
    if not knowledge_context or knowledge_context == "NO_VERIFIED_CONTEXT_AVAILABLE" or "RED_FLAG_DETECTED:" in knowledge_context:
        raise KnowledgeBaseNotReady("No verified authoritative hair-care context is available for routine generation.")

    prompt_template = HAIR_CARE_PLAN_REVISION_PROMPT if modification_instruction else HAIR_CARE_PLAN_PROMPT
    prompt_values = {
        "profile": profile,
        "recent_plans": recent_plans or ["No previous hair-care routines."],
        "day_number": day_number,
        "knowledge_context": knowledge_context,
    }
    if modification_instruction:
        prompt_values["current_plan_text"] = current_plan_text or "No saved routine available."
        prompt_values["modification_instruction"] = modification_instruction
    base_prompt = prompt_template.format(**prompt_values)
    repair_feedback = ""
    last_problems: list[str] = []

    for attempt in range(2):
        prompt = base_prompt
        if repair_feedback:
            prompt += "\n\nSAFETY REPAIR REQUIRED:\n" + repair_feedback
        response = await _generate(
            settings.gemini_model_hair_plan,
            [types.Content(role="user", parts=[types.Part(text=prompt)])],
            types.GenerateContentConfig(
                temperature=0.15,
                response_mime_type="application/json",
                response_schema=HairCarePlanOutput,
            ),
        )
        try:
            plan = HairCarePlanOutput.model_validate_json(response.text)
        except (ValidationError, TypeError, json.JSONDecodeError) as exc:
            last_problems = [f"Invalid structured hair-care routine: {str(exc)[:250]}"]
            repair_feedback = "Return all required structured fields as valid JSON. " + last_problems[0]
            continue
        last_problems = _hair_plan_validation_problems(plan)
        if not last_problems:
            return _render_hair_care_plan(plan, day_number)
        repair_feedback = "Remove these unsafe or overly long elements: " + "; ".join(last_problems)

    logger.error("hair_care_plan_generation_validation_exhausted", day_number=day_number, problems=last_problems)
    raise PlanGenerationValidationError(last_problems or ["Unknown hair-care routine validation failure."])


async def update_conversation_summary(existing_summary, recent_messages):
    if not recent_messages:
        return existing_summary or ""
    # Durable memory should be built from user-authored facts rather than assistant
    # replies. This prevents profile-recall boilerplate and saved-plan payloads from
    # becoming self-reinforcing context in later conversations.
    user_messages = [m for m in recent_messages if m.get("role") == "user"]
    if not user_messages:
        return existing_summary or ""
    formatted = "\n".join(f"USER: {m['content']}" for m in user_messages)
    prompt = SUMMARY_UPDATE_PROMPT.format(
        existing_summary=existing_summary or "None",
        recent_messages=formatted,
    )
    response = await _generate(
        settings.gemini_model_chat,
        [types.Content(role="user", parts=[types.Part(text=prompt)])],
        None,
    )
    return response.text or existing_summary or ""


async def close_client() -> None:
    await client.aio.aclose()


async def classify_conversation(
    profile: dict,
    history: list[dict],
    user_message: str,
    summary: str | None = None,
) -> ConversationRoute:
    """Semantic routing with structured output. No side effects occur here."""
    recent = history[-8:] if history else []
    formatted_history = "\n".join(
        f"{item.get('role', '').upper()}: {item.get('content', '')}" for item in recent
    ) or "No recent conversation."

    prompt = CONVERSATION_ROUTER_PROMPT.format(
        user_message=user_message,
        history=formatted_history,
        summary=summary or "No long-term summary yet.",
        profile=profile,
    )

    response = await _generate(
        settings.gemini_model_router,
        [types.Content(role="user", parts=[types.Part(text=prompt)])],
        types.GenerateContentConfig(
            temperature=0,
            max_output_tokens=256,
            response_mime_type="application/json",
            response_schema=ConversationRoute,
        ),
    )

    try:
        return ConversationRoute.model_validate_json(response.text)
    except (ValidationError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Conversation router returned invalid structured output: {exc}") from exc
