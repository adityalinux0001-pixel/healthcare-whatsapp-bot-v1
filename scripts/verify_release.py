#!/usr/bin/env python3
"""Static release gate that does not require Docker or external API calls."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
required = [
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.dev.yml",
    "requirements.txt",
    "alembic.ini",
    "app/main.py",
    "app/worker.py",
    "scripts/build_knowledge_base.py",
    "migrations/versions/0004_health_consent.py",
    "migrations/versions/0005_profile_safety_and_webhook_idempotency.py",
    "migrations/versions/0006_diet_plan_delivery_provider_id.py",
    "app/services/profile_validation.py",
    "app/services/plan_validation.py",
    "app/llm/schemas.py",
    "data/sources/external/authoritative_sources.json",
]
missing = [p for p in required if not (ROOT / p).exists()]
if missing:
    print("MISSING:", *missing, sep="\n")
    sys.exit(1)
if (ROOT / ".env").exists():
    print("ERROR: .env must not be shipped in the release archive.")
    sys.exit(1)
print("Release static checks passed.")
