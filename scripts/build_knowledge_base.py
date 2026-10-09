from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import settings
from app.knowledge.embeddings import get_embedder
from app.knowledge.store import COLLECTION_NAME, get_client

ROOT = Path(__file__).resolve().parents[1]
HAIR_SOURCE = ROOT / "data" / "sources" / "external" / "hair_loss_guidelines.json"
CHROMA_DIR = Path(settings.knowledge_chroma_dir)
MANIFEST = ROOT / "data" / "knowledge" / "manifest.json"


def add_documents(collection, records: list[dict], meta: dict) -> int:
    texts: list[str] = []
    ids: list[str] = []
    metas: list[dict] = []
    for item in records:
        text = f"Topic: {item['topic']}. Facts: " + " ".join(item["facts"])
        stable = hashlib.sha256((meta["source_name"] + "|" + text).encode("utf-8")).hexdigest()[:24]
        ids.append(f"kb_{stable}")
        texts.append(text)
        metas.append({
            **meta,
            "topic": item["topic"],
            "source_urls": " | ".join(item.get("source_urls", [])),
        })

    if not texts:
        return 0

    embeddings = get_embedder().encode(texts, normalize_embeddings=True, show_progress_bar=True).tolist()
    for start in range(0, len(texts), 256):
        collection.upsert(
            ids=ids[start:start + 256],
            documents=texts[start:start + 256],
            metadatas=metas[start:start + 256],
            embeddings=embeddings[start:start + 256],
        )
    return len(texts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Hair & Scalp Assistant knowledge base.")
    parser.add_argument(
        "--replace", action="store_true",
        help="Clear the current collection before rebuilding it with hair/scalp sources only.",
    )
    args = parser.parse_args()

    if not HAIR_SOURCE.exists():
        raise FileNotFoundError(f"Required hair/scalp source file not found: {HAIR_SOURCE}")

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "knowledge").mkdir(parents=True, exist_ok=True)
    client = get_client()
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "Hair and scalp authoritative guidance only", "hnsw:space": "cosine"},
    )

    if args.replace and collection.count():
        existing_ids = collection.get(include=[]).get("ids", [])
        for start in range(0, len(existing_ids), 500):
            collection.delete(ids=existing_ids[start:start + 500])

    source_data = json.loads(HAIR_SOURCE.read_text(encoding="utf-8"))
    metadata = {
        "source_name": source_data["source_name"],
        "source_type": source_data["source_type"],
        "source_role": "authoritative",
        "authority": float(source_data["authority"]),
        "source_reference": "Paraphrased summaries of AAD and NHS public guidance; consult original source URLs.",
        "license_status": "PARAPHRASED_SUMMARY_SOURCE_TERMS_REVIEW_REQUIRED",
    }
    count = add_documents(collection, source_data.get("sources", []), metadata)
    manifest = {
        "schema_version": 3,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "domain": "hair_and_scalp",
        "sources": [{
            "file": str(HAIR_SOURCE.relative_to(ROOT)),
            "sha256": hashlib.sha256(HAIR_SOURCE.read_bytes()).hexdigest(),
            "documents_indexed": count,
            **metadata,
        }],
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Done. Hair/scalp documents indexed: {count}; total collection count: {collection.count()}")
    print(f"Manifest: {MANIFEST}")


if __name__ == "__main__":
    main()
