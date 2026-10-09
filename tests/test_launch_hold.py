from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_launch_hold_message_is_clear_and_non_promissory_beyond_eta():
    message = (ROOT / "app/services/launch_mode.py").read_text(encoding="utf-8")
    assert "2–3 business days" in message
    assert "subscription is active" in message
    assert "general hair and scalp-care questions" in message
    assert "does not diagnose" in message.casefold()


def test_launch_hold_default_and_all_daily_send_entry_points_are_guarded():
    config = (ROOT / "app/config.py").read_text(encoding="utf-8")
    worker = (ROOT / "app/worker.py").read_text(encoding="utf-8")
    plan_service = (ROOT / "app/services/hair_care_plan_service.py").read_text(encoding="utf-8")
    conversation = (ROOT / "app/services/conversation_service.py").read_text(encoding="utf-8")

    assert "hair_care_launch_hold: bool = True" in config
    assert worker.count("if settings.hair_care_launch_hold") >= 3
    assert "if settings.hair_care_launch_hold or not is_routine_eligible_user(user)" in plan_service
    assert "hair_care_launch_hold_direct_generation_skipped" in plan_service
    assert "hair_care_launch_hold_direct_delivery_skipped" in plan_service
    assert "if settings.hair_care_launch_hold or not modification_instruction.strip()" in plan_service
    assert "if settings.hair_care_launch_hold:" in conversation


def test_render_propagates_launch_hold_to_api_and_worker():
    render = (ROOT / "render.yaml").read_text(encoding="utf-8")
    assert "- key: HAIR_CARE_LAUNCH_HOLD\n        value: \"true\"" in render
    assert "envVarKey: HAIR_CARE_LAUNCH_HOLD" in render


def test_knowledge_builder_only_indexes_hair_guidance():
    builder = (ROOT / "scripts/build_knowledge_base.py").read_text(encoding="utf-8")
    assert "hair_loss_guidelines.json" in builder
    assert "DGI_2024" not in builder
    assert "pd.read_csv" not in builder
    assert "PdfReader" not in builder


def test_legacy_source_assets_are_removed_but_history_migrations_are_preserved():
    assert not (ROOT / "data/sources/raw").exists()
    assert not (ROOT / "data/sources/external/exercise_guidelines.json").exists()
    assert not (ROOT / "data/sources/external/authoritative_sources.json").exists()
    assert (ROOT / "migrations/versions/0001_init.py").exists()
    assert (ROOT / "migrations/versions/0010_hair_care_plans.py").exists()
    models = (ROOT / "app/models.py").read_text(encoding="utf-8")
    assert 'class DietPlan(Base)' in models  # historical records remain queryable in admin
