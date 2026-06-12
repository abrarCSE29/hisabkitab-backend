# HisabKitab (হিসাবকিতাব) — Backend

FastAPI backend for the HisabKitab family expense tracker. See
[System-Architecture-Hisabkitab.md](System-Architecture-Hisabkitab.md) for the
full architectural blueprint.

## Stack

- **FastAPI** (Python 3.12) with sync `def` routes — PyMongo calls run in the framework thread pool
- **MongoDB** via PyMongo (Atlas M0 in production, Docker locally)
- **Supabase Auth** — JWTs verified locally with the project JWT secret (HS256)
- **Groq vision models** (OpenAI-compatible API, model set via `OCR_MODEL`) for receipt OCR
- **uv** for dependency management, **pytest + mongomock** for tests

## Getting started

```bash
# 1. Install dependencies
uv sync

# 2. Start local MongoDB
docker compose up -d

# 3. Configure environment
cp .env.example .env   # then fill in real values

# 4. Run the API (http://localhost:8000/docs)
uv run uvicorn app.main:app --reload
```

## Tests

No MongoDB or external services needed — the suite uses mongomock and mocked
OCR/JWT fixtures:

```bash
uv run pytest
```

## Logging

Every request emits one structured line on the `hisabkitab.request` logger —
method, path, status, duration, and the verified Supabase user id:

```
2026-06-10 02:18:30,696 INFO [hisabkitab.request] GET /api/v1/health -> 200 (1.1 ms) user=-
2026-06-10 02:18:30,703 WARNING [app.core.security] Rejected request without bearer token: /api/v1/vouchers
```

Auth rejections (missing/expired/invalid tokens) are logged as warnings, and
unhandled exceptions are logged with a full stack trace. Logs go to stdout
(which Render/Koyeb capture in their dashboards) and to a size-rotated
`server.log` in the working directory (5 MB, 3 backups). Set `LOG_FILE=`
(empty) to disable the file, or `DEBUG=true` to lower the level to DEBUG.

## API overview

All routes except `/api/v1/health` require `Authorization: Bearer <supabase-jwt>`.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/api/v1/health` | Health check / keep-alive ping target |
| GET | `/api/v1/auth/me` | Verify session, return user identity |
| POST | `/api/v1/vouchers` | Create voucher (quick or multi-item; FR-2) |
| GET | `/api/v1/vouchers?family_id=&limit=` | Reverse-chronological feed, solo or family scoped |
| POST | `/api/v1/vouchers/ocr` | Extract receipt items via Groq vision model (FR-6) |
| GET | `/api/v1/categories?type=` | Bilingual Bangla/English categories (FR-3) |
| POST | `/api/v1/family` | Create family group, caller becomes admin (FR-4) |
| GET | `/api/v1/family` | List the caller's families |
| POST | `/api/v1/family/invite` | Email a single-use join code (admin only) |
| POST | `/api/v1/family/join` | Redeem a join code |

A ready-made Postman collection lives in
[postman/HisabKitab.postman_collection.json](postman/HisabKitab.postman_collection.json)
(set the `baseUrl` and `token` collection variables).

## Project layout

```
app/
├── main.py               # app factory + Mongo lifespan
├── core/
│   ├── config.py         # pydantic-settings (.env)
│   ├── security.py       # Supabase JWT verification (FR-1)
│   ├── categories.py     # bilingual category registry (FR-3)
│   └── storage.py        # receipt image URL validation (FR-5)
├── db/mongodb.py         # PyMongo pool lifecycle + indexes
├── schemas/              # Pydantic request/response models
├── services/             # vouchers, families, ocr, email
└── api/v1/endpoints/     # route handlers
tests/                    # pytest suite (81 tests)
```

## Deployment (Koyeb / Render free tier)

Build the included [Dockerfile](Dockerfile); the container honors the `PORT`
env var. Set `MONGODB_URI`, `SUPABASE_URL`, `SUPABASE_JWT_SECRET`, and
`GROQ_API_KEY` in the platform dashboard, and point a free cron service
(e.g. Cron-Job.org) at `/api/v1/health` every 10 minutes to prevent free-tier
sleep.
