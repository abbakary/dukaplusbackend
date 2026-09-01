import os
from urllib.parse import parse_qs, urlparse, urlunparse

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _fix_sqlite_url(url: str) -> str:
    """Accept sqlite+aiosqlite:/./path (two slashes) as well as the canonical three-slash form."""
    if url.startswith("sqlite+aiosqlite:/") and not url.startswith("sqlite+aiosqlite:///"):
        return "sqlite+aiosqlite:///" + url[len("sqlite+aiosqlite:/") :].lstrip("/")
    return url


def normalize_database_url(url: str) -> tuple[str, bool]:
    """Convert Railway/Heroku postgres URLs to async SQLAlchemy + asyncpg."""
    url = _fix_sqlite_url(url.strip())
    if url.startswith("sqlite"):
        return url, False

    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]

    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    needs_ssl = query.get("sslmode", [""])[0].lower() in {"require", "verify-full", "verify-ca"}

    # asyncpg does not accept libpq query params like sslmode on the URL
    clean = urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, "", parsed.fragment))

    if clean.startswith("postgresql://") and "+asyncpg" not in clean and "+psycopg" not in clean:
        clean = "postgresql+asyncpg://" + clean[len("postgresql://") :]

    return clean, needs_ssl


def _pick_database_url(explicit: object) -> str:
    """Resolve DB URL from explicit value or common Railway env vars."""
    candidates: list[str] = []
    if isinstance(explicit, str) and explicit.strip():
        candidates.append(explicit.strip())

    for key in (
        "DATABASE_URL",
        "DATABASE_PRIVATE_URL",
        "POSTGRES_URL",
        "POSTGRES_PRIVATE_URL",
        "PGDATABASE_URL",
    ):
        env_val = os.environ.get(key, "").strip()
        if env_val and env_val not in candidates:
            candidates.append(env_val)

    if candidates:
        return candidates[0]
    return "sqlite+aiosqlite:///./dukaplus.db"


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

    @field_validator("database_url", mode="before")
    @classmethod
    def resolve_database_url(cls, v: object) -> str:
        return _fix_sqlite_url(_pick_database_url(v))

    @model_validator(mode="after")
    def validate_production_database(self) -> "Settings":
        url = self.database_url.strip()
        if self.is_production and (not url or url.startswith("sqlite")):
            raise ValueError(
                "DATABASE_URL is empty or missing in production. "
                "In Railway → dukaplusbackend service → Variables → Add Reference → "
                "select your Postgres service → DATABASE_PRIVATE_URL → name it DATABASE_URL. "
                "Then redeploy."
            )
        return self

    @property
    def async_database_url(self) -> str:
        url, _ = normalize_database_url(self.database_url)
        return url

    @property
    def database_ssl_required(self) -> bool:
        _, needs_ssl = normalize_database_url(self.database_url)
        return needs_ssl

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


settings = Settings()
