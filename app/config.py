
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"

    database_url: str

    @field_validator("database_url", mode="before")
    @classmethod
    def normalize_database_url(cls, value: str) -> str:
        if not isinstance(value, str):
            return value

        # Render/PostgreSQL commonly supplies one of these URL formats.
        # The application and Alembic use SQLAlchemy's async engine,
        # so normalize them to the asyncpg driver.
        if value.startswith("postgres://"):
            return "postgresql+asyncpg://" + value[len("postgres://"):]

        if value.startswith("postgresql://"):
            return "postgresql+asyncpg://" + value[len("postgresql://"):]

        return value

    redis_url: str = "redis://localhost:6379/0"

    whatsapp_token: str
    whatsapp_phone_number_id: str
    whatsapp_verify_token: str
    whatsapp_app_secret: str
    whatsapp_api_version: str = "v21.0"
    whatsapp_daily_plan_template_name: str = ""
    whatsapp_daily_plan_template_language_code: str = "en"

    gemini_api_key: str
    gemini_model_chat: str = "gemini-2.5-flash-lite"
    gemini_model_diet_plan: str = "gemini-2.5-flash"

    razorpay_key_id: str
    razorpay_key_secret: str
    razorpay_webhook_secret: str

    subscription_price_inr: int = 499
    subscription_days: int = 21

    knowledge_chroma_dir: str = "./data/knowledge/chroma"
    knowledge_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    knowledge_top_k_diet: int = 10
    knowledge_top_k_qa: int = 8
    knowledge_top_k_exercise: int = 5
    knowledge_authoritative_query_expansions: int = 4
    knowledge_min_authoritative_results: int = 2
    knowledge_min_similarity: float = 0.25
    knowledge_min_authoritative_score: float = 0.50

    worker_heartbeat_required: bool = False
    worker_heartbeat_key: str = "dietbot:worker:heartbeat"

    # Production safety gate.
    # Keep False for local/dev compatibility; set True in production.
    require_health_consent: bool = False

    conversation_lock_seconds: int = 900

    bypass_subscription: bool = False

    # Admin dashboard (single admin; set these in the same .env as the chatbot).
    admin_phone: str = ""
    admin_password_hash: str = ""
    admin_session_secret: str = ""
    admin_session_max_age: int = 28800
    admin_cookie_secure: bool = False


settings = Settings()

