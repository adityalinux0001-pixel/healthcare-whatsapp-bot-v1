
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import pandas as pd
from pypdf import PdfReader

from app.config import settings
from app.knowledge.embeddings import get_embedder
from app.knowledge.store import COLLECTION_NAME, get_client

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "sources" / "raw"
EXTERNAL = ROOT / "data" / "sources" / "external"
CHROMA_DIR = Path(settings.knowledge_chroma_dir)
MANIFEST = ROOT / "data" / "knowledge" / "manifest.json"

SOURCE_META = {
    "DGI_2024.pdf": {
        "source_name": "ICMR-NIN Dietary Guidelines for Indians 2024",
        "source_type": "official_guideline",
        "source_role": "authoritative",
        "authority": 1.00,
        "source_reference": "ICMR-NIN DGI 2024",
        "source_url": "https://www.nin.res.in/dietaryguidelines/pdfjs/locale/DGI_2024.pdf",
        "license_status": "REVIEW_REQUIRED_FOR_ELECTRONIC_PRODUCT_USE",
    },
    "personalized_diet_recommendations.csv": {
        "source_name": "Personalized Diet Recommendations dataset",
        "source_type": "dataset",
        "source_role": "supporting",
        "authority": 0.30,
        "source_reference": "Kaggle dataset; examples only; not clinical evidence",
        "license_status": "REVIEW_REQUIRED",
    },
    "diet_recommendations.csv": {
        "source_name": "Diet Recommendations dataset",
        "source_type": "dataset",
        "source_role": "supporting",
        "authority": 0.30,
        "source_reference": "Kaggle dataset; examples only; not clinical evidence",
        "license_status": "REVIEW_REQUIRED",
    },
    "food_behavior_survey.csv": {
        "source_name": "Food behavior survey dataset",
        "source_type": "survey",
        "source_role": "supporting_only",
        "authority": 0.05,
        "source_reference": "Behavioral survey; never health evidence",
    },
    "def_survey_responses.csv": {
        "source_name": "DEF health/fitness survey responses",
        "source_type": "survey",
        "source_role": "supporting_only",
        "authority": 0.05,
        "source_reference": "Survey responses; never health evidence",
    },
}

EXERCISE_SOURCE = EXTERNAL / "exercise_guidelines.json"


def chunk_text(text: str, *, size: int = 1600, overlap: int = 250) -> list[str]:
    text = " ".join(text.split())
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


def csv_documents(path: Path):
    df = pd.read_csv(path).drop_duplicates().reset_index(drop=True)
    for row_number, row in df.iterrows():
        parts = []
        for col, value in row.items():
            if pd.isna(value):
                continue
            parts.append(f"{col}: {value}")
        yield "Dataset row. " + "; ".join(parts), {"row_number": int(row_number + 1)}


def pdf_documents(path: Path):
    reader = PdfReader(str(path))
    for page_no, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        for chunk_no, chunk in enumerate(chunk_text(text), start=1):
            yield chunk, {"page_number": page_no, "chunk_number": chunk_no}


def add_documents(collection, docs, meta):
    texts: list[str] = []
    ids: list[str] = []
    metas: list[dict] = []
    for text, extra in docs:
        stable = hashlib.sha256(
            (meta["source_name"] + "|" + text).encode("utf-8", errors="ignore")
        ).hexdigest()[:24]
        ids.append(f"kb_{stable}")
        texts.append(text)
        metas.append({**meta, **extra})
    if not texts:
        return 0

    embeddings = get_embedder().encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=True,
    ).tolist()
    for start in range(0, len(texts), 256):
        collection.upsert(
            ids=ids[start:start + 256],
            documents=texts[start:start + 256],
            metadatas=metas[start:start + 256],
            embeddings=embeddings[start:start + 256],
        )
    return len(texts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--include-supporting",
        action="store_true",
        help="Also index non-clinical supporting recommendation datasets; surveys remain excluded.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Explicitly clear the existing collection before rebuilding it.",
    )
    args = parser.parse_args()

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "data" / "knowledge").mkdir(parents=True, exist_ok=True)
    client = get_client()
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "Health Assistant RAG KB", "hnsw:space": "cosine"},
    )

    if args.replace and collection.count():
        existing_ids = collection.get(include=[]).get("ids", [])
        if existing_ids:
            collection.delete(ids=existing_ids)

    manifest = {
        "schema_version": 2,
        "built_at": pd.Timestamp.utcnow().isoformat(),
        "include_supporting": args.include_supporting,
        "sources": [],
    }

    for filename, meta in SOURCE_META.items():
        # Survey files are intentionally never indexed into the runtime KB.
        if meta["source_role"] == "supporting_only":
            continue
        if meta["source_role"] == "supporting" and not args.include_supporting:
            continue

        path = RAW / filename
        if not path.exists():
            print(f"SKIP missing: {path}")
            continue
        docs = pdf_documents(path) if path.suffix.lower() == ".pdf" else csv_documents(path)
        count = add_documents(collection, docs, meta)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest["sources"].append({
            "file": str(path.relative_to(ROOT)),
            "sha256": digest,
            "documents_indexed": count,
            **meta,
        })
        print(f"Indexed {filename}: {count} documents")

    if EXERCISE_SOURCE.exists():
        exercise_data = json.loads(EXERCISE_SOURCE.read_text(encoding="utf-8"))
        exercise_docs = []
        for item in exercise_data.get("sources", []):
            exercise_docs.append(
                (
                    f"Topic: {item['topic']}. Facts: " + " ".join(item["facts"]),
                    {
                        "topic": item["topic"],
                        "source_urls": " | ".join(item.get("source_urls", [])),
                    },
                )
            )
        meta = {
            "source_name": exercise_data["source_name"],
            "source_type": exercise_data["source_type"],
            "source_role": "authoritative",
            "authority": float(exercise_data["authority"]),
            "source_reference": "WHO/CDC official guidance; summarized for retrieval",
        }
        count = add_documents(collection, exercise_docs, meta)
        digest = hashlib.sha256(EXERCISE_SOURCE.read_bytes()).hexdigest()
        manifest["sources"].append({
            "file": str(EXERCISE_SOURCE.relative_to(ROOT)),
            "sha256": digest,
            "documents_indexed": count,
            **meta,
        })

    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Done. Chroma documents: {collection.count()}")
    print(f"Manifest: {MANIFEST}")


if __name__ == "__main__":
    main()
