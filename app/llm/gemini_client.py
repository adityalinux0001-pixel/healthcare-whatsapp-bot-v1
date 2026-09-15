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
from app.knowledge.retrieval import (
    retrieve_diet_context,
    retrieve_general_context,
    retrieve_exercise_context,
)
from app.knowledge.safety import detect_red_flag, emergency_response
from app.knowledge.store import KnowledgeBaseNotReady
from app.llm.prompts import (
    ONBOARDING_SYSTEM_PROMPT,
    GENERAL_QA_SYSTEM_PROMPT,
    DIET_PLAN_PROMPT,
    DIET_PLAN_REVISION_PROMPT,
    SUMMARY_UPDATE_PROMPT,
    UPDATE_PROFILE_FUNCTION,
    GET_DIET_PLAN_FUNCTION,
)
from app.llm.schemas import DietPlanOutput
from app.services.plan_validation import validate_plan

client = genai.Client(api_key=settings.gemini_api_key)
_update_profile_tool = types.Tool(function_declarations=[UPDATE_PROFILE_FUNCTION])
_get_diet_plan_tool = types.Tool(function_declarations=[GET_DIET_PLAN_FUNCTION])


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
    diet_plan_request: dict | None = None
    tool_calls: list[tuple[str, dict, str | None]] = []
    candidate = response.candidates[0]
    reply_text = ""
    for part in candidate.content.parts:
        fn = getattr(part, "function_call", None)
        if fn:
            args = {k: v for k, v in fn.args.items() if v not in (None, "")}
            name = getattr(fn, "name", "")
            tool_calls.append((name, args, getattr(fn, "id", None)))
            if name == "get_diet_plan":
                diet_plan_request = args
            else:
                extracted.update(args)
        if getattr(part, "text", None):
            reply_text += part.text
    return extracted, diet_plan_request, candidate.content, reply_text, tool_calls


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


async def run_onboarding_turn(
    profile, missing_fields, history, user_message, summary=None, explicit_fields=None
):
    explicit_fields = explicit_fields or {}
    latest_assistant_question = next(
        (str(turn.get("content") or "").strip() for turn in reversed(history) if turn.get("role") == "assistant"),
        "",
    )
    state_hint = (
        "\n\nCURRENT TURN CONTEXT:\n"
        f"Latest assistant message/question: {latest_assistant_question or 'Not available'}\n"
        "Interpret the user's current message as an answer to that message when appropriate. "
        "A short reply such as 'no', 'none', or 'I don't have any' is meaningful only when the preceding question clearly establishes what it refers to. "
    )
    if explicit_fields:
        state_hint += (
            "\nCURRENT-TURN HIGH-CONFIDENCE EXTRACTION (treat only these values as explicitly stated by the user):\n"
            f"{explicit_fields}\n"
            "These values are authoritative for this turn. Do not contradict them in your reply. "
            "In particular, never turn veg/vegetarian into vegan unless the user explicitly said vegan."
        )

    system_prompt = ONBOARDING_SYSTEM_PROMPT.format(
        missing_fields=missing_fields,
        profile=profile,
        summary=summary or "No summary yet.",
    ) + state_hint
    contents = _history_to_contents(history)
    contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))
    response = await _generate(
        settings.gemini_model_chat,
        contents,
        types.GenerateContentConfig(system_instruction=system_prompt, tools=[_update_profile_tool]),
    )
    extracted, _, candidate_content, reply_text, tool_calls = _extract_function_calls(response)
    if tool_calls and extracted and not reply_text and candidate_content:
        # Give the final conversational response an up-to-date, authoritative view of the
        # current turn. This prevents stale-profile follow-ups from re-asking filled fields
        # or describing vegetarian users as vegan.
        accepted = {**extracted, **explicit_fields}
        merged_profile = {**profile, **accepted}
        remaining = [
            field for field in missing_fields
            if field not in accepted or accepted.get(field) in (None, "")
        ]
        followup_prompt = ONBOARDING_SYSTEM_PROMPT.format(
            missing_fields=remaining,
            profile=merged_profile,
            summary=summary or "No summary yet.",
        )
        followup_prompt += (
            "\n\nAUTHORITATIVE CURRENT-TURN FACTS ALREADY CAPTURED:\n"
            f"{accepted}\n"
            "Do not ask again about any field present above. Do not contradict these facts. "
            "Never call a vegetarian user vegan unless vegan was explicitly stated. "
            "Use English only. Respond naturally and ask only about the remaining missing fields, maximum 1-2 at a time."
        )
        reply_text = await _tool_followup(contents, candidate_content, tool_calls, followup_prompt)
    return reply_text or "Understood. Let's continue.", extracted


async def run_general_qa(profile, history, user_message, summary=None):
    red_flag = detect_red_flag(user_message)
    if red_flag:
        return emergency_response(), {}, None

    try:
        knowledge_context = await retrieve_general_context(profile, user_message, top_k=settings.knowledge_top_k_qa)
    except KnowledgeBaseNotReady:
        return (
            "I do not have enough verified health knowledge context to answer safely right now, so I will not guess. "
            "Please try again later.",
            {},
            None,
        )

    system_prompt = GENERAL_QA_SYSTEM_PROMPT.format(
        today_date=datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat(),
        profile=profile,
        summary=summary or "No summary yet.",
        knowledge_context=knowledge_context,
    )
    contents = _history_to_contents(history)
    contents.append(types.Content(role="user", parts=[types.Part(text=user_message)]))
    response = await _generate(
        settings.gemini_model_chat,
        contents,
        types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[_update_profile_tool, _get_diet_plan_tool],
        ),
    )
    extracted, plan_request, candidate_content, reply_text, tool_calls = _extract_function_calls(response)

    if tool_calls and extracted and not reply_text and candidate_content:
        update_calls = [call for call in tool_calls if call[0] == "update_profile"]
        reply_text = await _tool_followup(contents, candidate_content, update_calls, system_prompt)
    elif not reply_text and not plan_request and candidate_content:
        reply_text = await _tool_followup(contents, candidate_content, [], system_prompt)

    return reply_text or "Sorry, I could not understand that. Could you please rephrase?", extracted, plan_request


async def generate_diet_plan(
    profile, recent_meals, summary=None, day_number=1, modification_instruction: str | None = None
) -> str:
    try:
        diet_context = await retrieve_diet_context(
            profile, recent_meals or ["No recent meals recorded."], top_k=settings.knowledge_top_k_diet
        )
        exercise_context = await retrieve_exercise_context(profile, top_k=settings.knowledge_top_k_exercise)
    except KnowledgeBaseNotReady:
        raise

    if diet_context == "NO_VERIFIED_CONTEXT_AVAILABLE":
        raise KnowledgeBaseNotReady("No authoritative diet context was retrieved.")
    if exercise_context == "NO_VERIFIED_CONTEXT_AVAILABLE":
        raise KnowledgeBaseNotReady("No authoritative exercise context was retrieved.")

    knowledge_context = (
        "AUTHORITATIVE DIET/NUTRITION CONTEXT:\n" + diet_context +
        "\n\nAUTHORITATIVE EXERCISE CONTEXT:\n" + exercise_context
    )
    prompt_template = DIET_PLAN_REVISION_PROMPT if modification_instruction else DIET_PLAN_PROMPT
    prompt_values = {
        "profile": profile,
        "recent_meals": recent_meals or ["No recent meals recorded."],
        "summary": summary or "No long-term preferences recorded yet.",
        "day_number": day_number,
        "knowledge_context": knowledge_context,
    }
    if modification_instruction:
        prompt_values["modification_instruction"] = modification_instruction
    base_prompt = prompt_template.format(**prompt_values)

    repair_feedback = ""
    for attempt in range(2):
        prompt = base_prompt
        if repair_feedback:
            prompt += "\n\nIMPORTANT REPAIR REQUIRED:\n" + repair_feedback
        response = await _generate(
            settings.gemini_model_diet_plan,
            [types.Content(role="user", parts=[types.Part(text=prompt)])],
            types.GenerateContentConfig(
                temperature=0.15,
                response_mime_type="application/json",
                response_schema=DietPlanOutput,
            ),
        )
        try:
            plan = DietPlanOutput.model_validate_json(response.text)
        except (ValidationError, TypeError, json.JSONDecodeError) as exc:
            repair_feedback = f"Return valid structured plan fields only. Parsing error: {str(exc)[:300]}"
            continue
        problems = validate_plan(plan, profile)
        if not problems:
            return _render_plan(plan, day_number)
        repair_feedback = "; ".join(problems[:12])

    raise KnowledgeBaseNotReady("Generated plan failed deterministic safety/constraint validation.")


def _render_plan(plan: DietPlanOutput, day_number: int) -> str:
    return (
        f"🌿 *Day {day_number} — Today's Diet Plan*\n\n"
        f"🍳 *Breakfast:* {plan.breakfast}\n\n"
        f"🍎 *Mid-morning:* {plan.mid_morning_snack}\n\n"
        f"🍛 *Lunch:* {plan.lunch}\n\n"
        f"☕ *Evening snack:* {plan.evening_snack}\n\n"
        f"🥗 *Dinner:* {plan.dinner}\n\n"
        f"🏃 *Exercise:* {plan.exercise}\n\n"
        f"💧 *Hydration & routine:* {plan.hydration_and_routine}\n\n"
        f"⚠️ {plan.safety_note}"
    )


async def update_conversation_summary(existing_summary, recent_messages):
    if not recent_messages:
        return existing_summary or ""
    formatted = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in recent_messages)
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
