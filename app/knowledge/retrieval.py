"""High-level retrieval with separate authoritative and supporting lanes."""
from __future__ import annotations

import asyncio

from app.knowledge.safety import detect_red_flag
from app.knowledge.store import format_context, retrieve_multi, KnowledgeBaseNotReady
from app.config import settings


def _profile_text(profile: dict) -> str:
    return "; ".join(
        f"{k}={v}" for k, v in profile.items() if v not in (None, "", "None reported")
    )


def _authoritative_diet_queries(profile: dict, question: str) -> list[str]:
    profile_text = _profile_text(profile)
    return [
        question,
        f"ICMR-NIN Dietary Guidelines for Indians 2024: {question}",
        f"Indian dietary guidelines healthy eating balanced diet food groups portions: {question}",
        f"Evidence-based Indian nutrition guidance for this user. Profile: {profile_text}. Request: {question}",
    ]


def _authoritative_exercise_queries(profile: dict) -> list[str]:
    profile_text = _profile_text(profile)
    return [
        "WHO physical activity guidelines adults moderate vigorous muscle strengthening balance",
        "WHO CDC physical activity recommendations progression and age-appropriate exercise",
        f"Safe evidence-based exercise guidance for this user. Profile: {profile_text}",
    ]


def _retrieve_sync(queries: list[str], *, top_k: int, source_role: str) -> list[dict]:
    return retrieve_multi(queries, n_results=top_k, source_role=source_role)  # type: ignore[arg-type]


async def retrieve_diet_context(profile: dict, recent_meals: list[str], *, top_k: int = 10) -> str:
    question = (
        "Create an Indian daily diet plan consistent with the user's goal, diet preference, "
        "allergies, medical conditions and food dislikes. Avoid recently used meals. "
        f"Recent meals: {recent_meals[:3]}"
    )
    rows = await asyncio.to_thread(
        _retrieve_sync,
        _authoritative_diet_queries(profile, question),
        top_k=top_k,
        source_role="authoritative",
    )
    if len(rows) < settings.knowledge_min_authoritative_results:
        raise KnowledgeBaseNotReady("Not enough high-confidence authoritative diet context was retrieved.")
    return format_context(rows)


async def retrieve_general_context(profile: dict, question: str, *, top_k: int = 8) -> str:
    flag = detect_red_flag(question)
    if flag:
        return "RED_FLAG_DETECTED: " + flag

    rows = await asyncio.to_thread(
        _retrieve_sync,
        [
            question,
            f"ICMR-NIN Dietary Guidelines for Indians 2024: {question}",
            f"Indian nutrition and healthy diet guidance: {question}",
        ],
        top_k=top_k,
        source_role="authoritative",
    )
    if not rows:
        return "NO_VERIFIED_CONTEXT_AVAILABLE"
    return format_context(rows)


async def retrieve_exercise_context(profile: dict, *, top_k: int = 5) -> str:
    rows = await asyncio.to_thread(
        _retrieve_sync,
        _authoritative_exercise_queries(profile),
        top_k=top_k,
        source_role="authoritative",
    )
    if not rows:
        return "NO_VERIFIED_CONTEXT_AVAILABLE"
    return format_context(rows)
