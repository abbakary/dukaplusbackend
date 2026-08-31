# Duka+ Backend — Railway Deployment

## Quick Deploy

1. Create a new project on [Railway](https://railway.app)
2. Add a **PostgreSQL** database service
3. Deploy this `backend/` folder as a service (Dockerfile or Nixpacks)
4. Set environment variables (see below)
5. Open your service URL — you should see the **Status & Admin Console** at `/`

## Required Environment Variables

Set these on your **backend API service** (not on the Postgres service):

| Variable | Value |
|----------|-------|
| `DATABASE_URL` | `${{ Postgres.DATABASE_PRIVATE_URL }}` |
| `SECRET_KEY` | Long random string for JWT signing |
| `ENVIRONMENT` | `production` |
| `CORS_ORIGINS` | Your frontend URL(s), comma-separated |
| `SUPER_ADMIN_EMAIL` | Platform admin login email |
| `SUPER_ADMIN_PASSWORD` | Strong password (not `admin123`) |
| `SEED_DEMO_DATA` | `false` |

### Linking Postgres on Railway

1. Create a **PostgreSQL** service in the same project.
2. Open your **backend** service → **Variables**.
3. Click **+ New Variable** → **Add Reference**.
4. Select the Postgres service → choose **`DATABASE_PRIVATE_URL`**.
5. Name the variable **`DATABASE_URL`** (our app reads this name).

Or paste manually:

```
DATABASE_URL=${{ Postgres.DATABASE_PRIVATE_URL }}
```

> **Important:** `Postgres` must match your Postgres **service name** in Railway exactly (case-sensitive). If you renamed it to `postgres`, use `${{ postgres.DATABASE_PRIVATE_URL }}`.

**Private vs public URL**

| Reference | When to use |
|-----------|-------------|
| `DATABASE_PRIVATE_URL` | Backend + DB in same Railway project (**recommended**) |
| `DATABASE_URL` | External tools or if private networking fails |

The backend auto-converts `postgresql://…` → `postgresql+asyncpg://…` for async SQLAlchemy.

Railway sets `PORT` automatically — the app reads it via `scripts/start.sh`. Do not hardcode `--port $PORT` in Procfile (Railway may pass it literally).

If deploy fails with `'$PORT' is not a valid integer`, redeploy after pulling the latest `scripts/start.sh` fix.

## Health Checks

| Endpoint | Purpose |
|----------|---------|
| `GET /` | Human-readable status + super-admin console |
| `GET /api/health` | Lightweight probe (Railway healthcheck) |
| `GET /api/health/detailed` | Full status with DB latency |
| `GET /api/ready` | Readiness probe (503 if DB down) |
| `GET /docs` | Swagger API documentation |

## Super Admin Console

After deploy, visit:

```
https://your-app.up.railway.app/
```

Sign in with your `SUPER_ADMIN_EMAIL` / `SUPER_ADMIN_PASSWORD` to:

- View live system status (API, database, tenant count)
- **Create new business accounts** (tenant + owner login)
- **Create additional super-admin users**
- Browse recent tenants

## API — Create Account (programmatic)

```bash
# 1. Login as super admin
curl -X POST https://your-app.up.railway.app/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@dukaplus.co.tz","password":"YOUR_PASSWORD"}'

# 2. Create tenant
curl -X POST https://your-app.up.railway.app/api/v1/admin/tenants \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "business_name": "Duka la Rehema",
    "owner_name": "Salum Omar",
    "email": "owner@example.co.tz",
    "phone": "+255712345678",
    "password": "secure123",
    "business_type": "retail",
    "region": "Dar es Salaam",
    "district": "Ilala",
    "plan": "free_starter",
    "status": "active"
  }'
```

## Local Development

```bash
cd backend
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 for the admin console.

## Frontend Connection

Point your React app API base URL to the Railway backend:

```
VITE_API_URL=https://your-app.up.railway.app/api/v1
```

Add the same URL to `CORS_ORIGINS` on the backend.
