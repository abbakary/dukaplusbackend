"""Production entrypoint — reads PORT from env (Railway-safe, no shell expansion)."""

import os
import sys

import uvicorn


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))

    # Fail fast with a readable message before SQLAlchemy import errors
    db_url = (
        os.environ.get("DATABASE_URL", "").strip()
        or os.environ.get("DATABASE_PRIVATE_URL", "").strip()
        or os.environ.get("POSTGRES_URL", "").strip()
        or os.environ.get("POSTGRES_PRIVATE_URL", "").strip()
    )
    env = os.environ.get("ENVIRONMENT", "development").lower()
    on_railway = bool(
        os.environ.get("RAILWAY_ENVIRONMENT")
        or os.environ.get("RAILWAY_PROJECT_ID")
        or os.environ.get("RAILWAY_SERVICE_ID")
    )
    if on_railway and not db_url:
        print(
            "\n[FATAL] Running on Railway but no Postgres URL is set.\n"
            "The API would fall back to ephemeral SQLite — tenant signups would NOT appear in Railway Postgres.\n"
            "Fix: dukaplusbackend → Variables → delete bad DATABASE_URL → Add Reference →\n"
            "Postgres → DATABASE_PRIVATE_URL → name it DATABASE_URL. Set ENVIRONMENT=production, redeploy.\n",
            file=sys.stderr,
        )
        sys.exit(1)
    if env in {"production", "prod"} and not db_url:
        print(
            "\n[FATAL] ENVIRONMENT=production but DATABASE_URL is missing.\n"
            "Fix: open dukaplusbackend service → Variables → Add Reference →\n"
            "Postgres service → DATABASE_PRIVATE_URL → variable name DATABASE_URL\n"
            "Delete any empty DATABASE_URL entry first, then redeploy.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    uvicorn.run("app.main:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
