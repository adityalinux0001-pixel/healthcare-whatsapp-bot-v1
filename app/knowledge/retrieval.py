"""Hair/scalp-specific retrieval over authoritative source summaries."""
from __future__ import annotations

import asyncio

from app.knowledge.safety import detect_red_flag
from app.knowledge.store import format_context, retrieve_multi
from app.config import settings


def _retrieve_sync(queries: list[str], *, top_k: int, source_role: str) -> list[dict]:
    return retrieve_multi(queries, n_results=top_k, source_role=source_role)


async def retrieve_general_context(profile: dict, question: str, *, top_k: int = 8) -> str:
    """Retrieve general authoritative hair/scalp context without indexing profile answers."""
    flag = detect_red_flag(question)
    if flag:
        return "RED_FLAG_DETECTED: " + flag

    rows = await asyncio.to_thread(
        _retrieve_sync,
        [
            question,
            f"American Academy of Dermatology evidence-based hair-loss and scalp guidance: {question}",
            f"Hair shedding, thinning, scalp symptoms, common causes, and when to see a dermatologist: {question}",
            f"Evidence-based hair care and nutrition guidance; do not infer causation from water hardness, sugar, or dairy intake alone: {question}",
        ],
        top_k=top_k,
        source_role="authoritative",
    )
    if not rows:
        return "NO_VERIFIED_CONTEXT_AVAILABLE"
    return format_context(rows)
