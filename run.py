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
    )
    env = os.environ.get("ENVIRONMENT", "development").lower()
    if env in {"production", "prod"} and not db_url:
        print(
            "\n[FATAL] DATABASE_URL is not set on Railway.\n"
            "Fix: open dukaplusbackend service → Variables → Add Reference →\n"
            "Postgres service → DATABASE_PRIVATE_URL → variable name DATABASE_URL\n"
            "Delete any empty DATABASE_URL entry first, then redeploy.\n",
            file=sys.stderr,
        )
        sys.exit(1)

    uvicorn.run("app.main:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
