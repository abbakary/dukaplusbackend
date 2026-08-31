from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_database_url(url: str) -> str:
    """Convert Railway/Heroku postgres URLs to async SQLAlchemy drivers."""
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+asyncpg" not in url and "+psycopg" not in url:
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./dukaplus.db"
    secret_key: str = "dev-secret-key-change-in-production"
    access_token_expire_days: int = 3
    refresh_token_expire_days: int = 30
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    gemini_api_key: str = ""
    environment: str = "development"
    app_name: str = "Duka+ API"
    app_version: str = "4.0.0"
    vat_rate: float = 0.18
    cache_ttl_seconds: int = 30
    analytics_cache_ttl_seconds: int = 60
    redis_url: str = ""
    port: int = 8000
    seed_demo_data: bool = True
    super_admin_email: str = "admin@dukaplus.co.tz"
    super_admin_password: str = "admin123"
    super_admin_name: str = "Platform Admin"
    super_admin_phone: str = "+255700000001"

    @property
    def async_database_url(self) -> str:
        return normalize_database_url(self.database_url)

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


settings = Settings()
