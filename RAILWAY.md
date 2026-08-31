# Duka+ Backend — Railway Deployment

## Quick Deploy

1. Create a new project on [Railway](https://railway.app)
2. Add a **PostgreSQL** database service
3. Deploy this `backend/` folder as a service (Dockerfile or Nixpacks)
4. Set environment variables (see below)
5. Open your service URL — you should see the **Status & Admin Console** at `/`

## Required Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Auto-linked from Railway Postgres (`postgresql://...`) |
| `SECRET_KEY` | Long random string for JWT signing |
| `ENVIRONMENT` | Set to `production` |
| `CORS_ORIGINS` | Your frontend URL(s), comma-separated |
| `SUPER_ADMIN_EMAIL` | Platform admin login email |
| `SUPER_ADMIN_PASSWORD` | Strong password (not `admin123`) |
| `SEED_DEMO_DATA` | Set to `false` in production |

Railway sets `PORT` automatically — do not override it.

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
