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
    "migrations/versions/0009_hair_loss_onboarding_profile.py",
    "migrations/versions/0010_hair_care_plans.py",
    "migrations/versions/0011_subscription_starts_on_first_routine.py",
    "app/services/subscription_periods.py",
    "app/services/hair_care_plan_service.py",
    "data/sources/external/hair_loss_guidelines.json",
    "app/services/profile_validation.py",
    "app/llm/schemas.py",
]
missing = [p for p in required if not (ROOT / p).exists()]
if missing:
    print("MISSING:", *missing, sep="\n")
    sys.exit(1)
# Product-domain safety gates: do not accidentally ship the old weight-loss cron.
worker_source = (ROOT / "app/worker.py").read_text(encoding="utf-8")
if "cron(daily_diet_plan_job" in worker_source or "cron(generate_and_send_daily_plans" in worker_source:
    print("ERROR: legacy daily diet-plan cron is still enabled.")
    sys.exit(1)
if "cron(daily_hair_care_plan_job, hour=6, minute=0)" not in worker_source:
    print("ERROR: daily hair-care plan cron is missing.")
    sys.exit(1)
config_source = (ROOT / "app/config.py").read_text(encoding="utf-8")
if "hair_care_launch_hold: bool = True" not in config_source:
    print("ERROR: temporary launch hold must default to enabled.")
    sys.exit(1)
if "hair_care_launch_hold" not in worker_source:
    print("ERROR: worker jobs are missing the temporary launch-hold guard.")
    sys.exit(1)
subscription_source = (ROOT / "app/services/subscription_periods.py").read_text(encoding="utf-8")
if "subscription.status = \"active\"" not in subscription_source or "subscription.start_date = started_at" not in subscription_source:
    print("ERROR: subscription period is not activated from first confirmed routine send.")
    sys.exit(1)
plan_service_source = (ROOT / "app/services/hair_care_plan_service.py").read_text(encoding="utf-8")
for required_guard in (
    "hair_care_launch_hold_direct_generation_skipped",
    "hair_care_launch_hold_direct_delivery_skipped",
):
    if required_guard not in plan_service_source:
        print(f"ERROR: direct plan path is missing launch-hold guard: {required_guard}")
        sys.exit(1)
for legacy_source in (
    "data/sources/raw/DGI_2024.pdf",
    "data/sources/raw/diet_recommendations.csv",
    "data/sources/raw/personalized_diet_recommendations.csv",
    "data/sources/raw/food_behavior_survey.csv",
    "data/sources/raw/def_survey_responses.csv",
    "data/sources/external/exercise_guidelines.json",
    "data/sources/external/authoritative_sources.json",
    "app/services/diet_plan_service.py",
    "app/services/plan_validation.py",
    "scripts/send_daily_plan_template_test.py",
):
    if (ROOT / legacy_source).exists():
        print(f"ERROR: obsolete weight-loss source/code remains: {legacy_source}")
        sys.exit(1)
if "legacy_diet_plan_job_skipped_hair_assistant" not in worker_source:
    print("ERROR: legacy queued diet-plan jobs are not guarded.")
    sys.exit(1)

if (ROOT / ".env").exists():
    print("ERROR: .env must not be shipped in the release archive.")
    sys.exit(1)
print("Release static checks passed.")
