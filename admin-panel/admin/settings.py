from pydantic_settings import BaseSettings, SettingsConfigDict


class AdminSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    database_url: str
    redis_url: str = "redis://localhost:6379/0"

    admin_phone: str
    admin_password_hash: str
    admin_session_secret: str
    admin_session_max_age: int = 28800
    admin_cookie_secure: bool = False

    admin_host: str = "127.0.0.1"
    admin_port: int = 8001


settings = AdminSettings()
