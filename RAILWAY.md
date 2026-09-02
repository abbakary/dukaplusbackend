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
| `CORS_ORIGINS` | Your frontend URL(s), comma-separated — include `https://dukaplusapp.vercel.app` |
| `SUPER_ADMIN_EMAIL` | Platform admin login email |
| `SUPER_ADMIN_PASSWORD` | Strong password (not `admin123`) |
| `SUPER_ADMIN_SYNC_PASSWORD` | `true` (default) — sync password from env on each deploy |
| `SEED_DEMO_DATA` | `true` to load 20 demo shops (30+ products/sales each) |

### Linking Postgres on Railway

**This crash means `DATABASE_URL` is empty:**

```
Could not parse SQLAlchemy URL from string ''
```

1. Open **dukaplusbackend** service (not Postgres) → **Variables**
2. **Delete** any `DATABASE_URL` that is blank or shows `${{...}}` unresolved
3. Click **+ New Variable** → **Add Reference** (or **Reference Variable**)
4. Select your **Postgres** service
5. Choose **`DATABASE_PRIVATE_URL`**
6. Set variable name to **`DATABASE_URL`**
7. Click **Deploy** / redeploy

Also set:

```
ENVIRONMENT=production
```

**Do not** paste `${{ Postgres.DATABASE_PRIVATE_URL }}` manually unless the Postgres service is literally named `Postgres`. Use **Add Reference** instead — Railway resolves it correctly.

| Reference | When to use |
|-----------|-------------|
| `DATABASE_PRIVATE_URL` | Backend + DB in same Railway project (**recommended**) |
| `DATABASE_URL` | Public URL if private networking fails |

## Fix: `'$PORT' is not a valid integer`

Railway must **not** use a custom start command like `uvicorn ... --port $PORT`.

1. Open your backend service in Railway → **Settings** → **Deploy**
2. Find **Custom Start Command** / **Start Command**
3. **Clear it completely** (leave empty) **OR** set exactly:
   ```
   python run.py
   ```
4. Redeploy

The app uses `run.py` which reads `PORT` in Python — no shell `$PORT` expansion needed.

Railway sets `PORT` automatically at runtime.

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
    "plan": "starter",
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

## Demo sample data on Railway (PostgreSQL)

Sample data is skipped unless `SEED_DEMO_DATA=true`.

### Option A — Auto-seed on deploy (recommended)

On **dukaplusbackend** → **Variables**:

```
SEED_DEMO_DATA=true
```

Redeploy. First startup creates **20 demo tenants** with **30 products, 30 customers, 30 sales** each, plus staff users for every role (Owner, Manager, Cashier, etc.). It also seeds **subscription expiry dates**, **M-Pesa payment history**, and **provider broadcasts** for the super admin portal.

Verify: `GET /api/health/detailed` → `tenant_count` should be **20+**.

Super admin is created automatically on every deploy (idempotent):

| Variable | Default |
|----------|---------|
| `SUPER_ADMIN_EMAIL` | `admin@dukaplus.co.tz` |
| `SUPER_ADMIN_PASSWORD` | `admin123` (set a strong value on Railway) |
| `SUPER_ADMIN_SYNC_PASSWORD` | `true` — keeps login password in sync with env |
| `SUPER_ADMIN_NAME` | `Platform Admin` |

**Login fails with 401?** Check Railway variables, then call:

```bash
curl -X POST https://dukaplusbackend-production.up.railway.app/api/v1/admin/bootstrap/super-admin
```

Then sign in with `SUPER_ADMIN_EMAIL` + `SUPER_ADMIN_PASSWORD` exactly as set on Railway (not `demo123`).

Verify: `GET /api/health/detailed` → `bootstrap_super_admin_exists` should be `true`.

### Option B — Railway Console

**dukaplusbackend** → **Console**:

```bash
python scripts/run_seed.py
```

### Option C — Super admin API

```bash
curl -X POST https://dukaplusbackend-production.up.railway.app/api/v1/admin/seed-demo \
  -H "Authorization: Bearer YOUR_SUPER_ADMIN_TOKEN"
```

### Demo logins (password: `demo123`)

| Email | Type | Role |
|-------|------|------|
| `pharmacy@sample.dukaplus.co.tz` | Pharmacy | Owner |
| `retail@sample.dukaplus.co.tz` | Retail | Owner |
| `restaurant@sample.dukaplus.co.tz` | Restaurant | Owner |
| `hardware@sample.dukaplus.co.tz` | Hardware | Owner |
| `electronics@sample.dukaplus.co.tz` | Electronics | Owner |
| `supermarket@sample.dukaplus.co.tz` | Supermarket | Owner |
| `manager.kariakoo-pharmacy@sample.dukaplus.co.tz` | Pharmacy | Manager |
| `cashier.mbezi-retail@sample.dukaplus.co.tz` | Retail | Cashier |
| `admin@dukaplus.co.tz` | Platform | Super Admin — use `SUPER_ADMIN_PASSWORD` from Railway (not `demo123`) |

Point your React app to Railway and sign in with any account above.

## Frontend Connection

Point your React app API base URL to the Railway backend (Vercel env var):

```
VITE_API_BASE_URL=https://dukaplusbackend-production.up.railway.app/api/v1
```

Add your Vercel URL to `CORS_ORIGINS` on the backend (or rely on the built-in `*.vercel.app` regex):

```
CORS_ORIGINS=https://dukaplusapp.vercel.app,http://localhost:5173
```
