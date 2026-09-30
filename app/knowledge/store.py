from __future__ import annotations

import threading
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import chromadb

from app.config import settings
from app.knowledge.embeddings import get_embedder
from app.utils.logging_config import logger

COLLECTION_NAME = "health_assistant_knowledge"
SourceRole = Literal["authoritative", "supporting"]


class KnowledgeBaseNotReady(RuntimeError):
    pass


# Serialises first-time Chroma initialisation across worker threads.
_init_lock = threading.RLock()


@lru_cache(maxsize=1)
def _get_client_cached() -> chromadb.PersistentClient:
    path = Path(settings.knowledge_chroma_dir)
    path.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(path))


def get_client() -> chromadb.PersistentClient:
    with _init_lock:
        return _get_client_cached()


@lru_cache(maxsize=1)
def _get_collection_cached():
    return get_client().get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={
            "description": "Grounded health, nutrition, food and exercise knowledge",
            "hnsw:space": "cosine",
        },
    )


def get_collection():
    with _init_lock:
        return _get_collection_cached()


def kb_ready() -> bool:
    try:
        count = get_collection().count()
    except Exception:
        # Do NOT hide the real error: log it so we can see why the KB check failed.
        logger.exception("kb_ready_check_failed", chroma_dir=settings.knowledge_chroma_dir)
        return False
    if count <= 0:
        logger.error("kb_collection_empty", chroma_dir=settings.knowledge_chroma_dir)
        return False
    return True


def _query_once(
    query: str,
    *,
    n_results: int,
    source_role: SourceRole | None,
) -> list[dict[str, Any]]:
    embedding = get_embedder().encode(query, normalize_embeddings=True).tolist()
    kwargs: dict[str, Any] = {
        "query_embeddings": [embedding],
        "n_results": max(n_results, 1),
        "include": ["documents", "metadatas", "distances"],
    }
    if source_role is not None:
        kwargs["where"] = {"source_role": source_role}

    result = get_collection().query(**kwargs)
    docs = result.get("documents", [[]])[0]
    metas = result.get("metadatas", [[]])[0]
    distances = result.get("distances", [[]])[0]
    ids = result.get("ids", [[]])[0]

    rows: list[dict[str, Any]] = []
    for doc_id, doc, meta, distance in zip(ids, docs, metas, distances):
        meta = meta or {}
        role = meta.get("source_role", "supporting")
        if role == "supporting_only":
            continue
        if source_role is not None and role != source_role:
            continue

        similarity = max(0.0, min(1.0, 1.0 - float(distance)))
        if similarity < settings.knowledge_min_similarity:
            continue

        authority = max(0.0, min(1.0, float(meta.get("authority", 0.2))))
        score = 0.70 * similarity + 0.30 * authority
        if source_role == "authoritative" and score < settings.knowledge_min_authoritative_score:
            continue

        rows.append(
            {
                "id": doc_id,
                "content": doc,
                "metadata": meta,
                "similarity": similarity,
                "score": score,
            }
        )

    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows


def retrieve(
    query: str,
    *,
    n_results: int = 8,
    include_supporting: bool = False,
    source_role: SourceRole | None = None,
) -> list[dict[str, Any]]:
    if not kb_ready():
        raise KnowledgeBaseNotReady(
            "Knowledge base is empty. Run scripts/build_knowledge_base.py first."
        )

    if source_role == "supporting" or include_supporting:
        return _query_once(query, n_results=n_results, source_role="supporting")

    return _query_once(
        query,
        n_results=max(n_results * 3, n_results),
        source_role="authoritative" if source_role is None else source_role,
    )


def retrieve_multi(
    queries: list[str],
    *,
    n_results: int = 8,
    source_role: SourceRole = "authoritative",
) -> list[dict[str, Any]]:
    if not kb_ready():
        raise KnowledgeBaseNotReady(
            "Knowledge base is empty. Run scripts/build_knowledge_base.py first."
        )

    merged: dict[str, dict[str, Any]] = {}
    expansion_limit = max(1, settings.knowledge_authoritative_query_expansions)
    per_query = max(3, min(n_results, 6))
    for query in queries[:expansion_limit]:
        for row in _query_once(query, n_results=per_query, source_role=source_role):
            current = merged.get(row["id"])
            if current is None or row["score"] > current["score"]:
                merged[row["id"]] = row

    rows = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
    return rows[:n_results]


def format_context(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "NO_VERIFIED_CONTEXT_AVAILABLE"

    chunks = []
    for i, row in enumerate(rows, start=1):
        m = row["metadata"]
        chunks.append(
            f"[SOURCE {i}]\n"
            f"role={m.get('source_role', 'unknown')}\n"
            f"authority={m.get('authority', 'unknown')}\n"
            f"similarity={row.get('similarity', 0):.3f}\n"
            f"source={m.get('source_name', 'unknown')}\n"
            f"type={m.get('source_type', 'unknown')}\n"
            f"reference={m.get('source_reference', 'unknown')}\n"
            f"source_url={m.get('source_url', m.get('source_urls', 'unknown'))}\n"
            f"content={row['content']}"
        )
    return "\n\n".join(chunks)