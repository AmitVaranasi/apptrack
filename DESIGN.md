# AppTrack — Full Design Document

> A self-hostable / deployable dashboard that reads a user's Gmail, detects job-application
> emails, classifies each into a pipeline stage, and lets the user click through to the exact
> email in Gmail. Built to start as a single-user tool and grow into a multi-tenant product.

## 0. Vision & principles

- **Email is the source of truth.** The app derives application state from real emails, not
manual data entry (manual edits are a correction layer, not the primary input).
- **Cheap before clever.** Filter aggressively with free heuristics; only spend LLM calls on
likely candidates.
- **Provider-swappable LLM.** All model calls go through one adapter so the model can change
without touching business logic.
- **Phase the risk.** Ship a single-user slice first; defer multi-tenant security and Google's
verification gauntlet until the idea is proven.

## 1. Tech stack


| Layer         | Choice                                                         | Rationale                                                                    |
| ------------- | -------------------------------------------------------------- | ---------------------------------------------------------------------------- |
| Backend       | **FastAPI** (Python, ASGI)                                     | Matches builder's Flask experience; great for JSON + HTML fragment endpoints |
| Templating    | **Jinja2**                                                     | Server-rendered pages + HTMX fragments                                       |
| Interactivity | **HTMX** + **Tailwind (CDN)**                                  | Minimal JS; partial-page swaps; fast to build                                |
| Host          | **Vercel** (Python serverless via `api/index.py`)              | Requested; cron available                                                    |
| Database      | **Supabase Postgres(supabase password: cymwaK-vuwxux-0qykra)** | Managed PG, RLS for Phase 3, free tier                                       |
| Auth          | **Direct Google OAuth 2.0** (Authlib)                          | Login + Gmail access in one flow                                             |
| Email         | **Gmail API** (`gmail.readonly`)                               | Stable message IDs for deep-links; History API for incremental sync          |
| LLM           | **Gemini 2.0 Flash** behind `Classifier` interface             | Free tier for prototype; swap to paid/Claude later                           |
| Sessions      | Signed HTTP-only cookies (`itsdangerous`)                      | No JS auth SDK needed                                                        |
| Secrets       | Vercel env vars; refresh tokens encrypted at rest (Fernet)     |                                                                              |


> **Why HTMX + FastAPI over Next.js:** HTMX swaps server-rendered HTML fragments, which pairs
> naturally with Jinja2 templates and the builder's Flask background. Next.js is React/JSX-first,
> so HTMX-in-Next fights the framework. HTMX is fully Vercel-compatible — it only needs endpoints
> that return HTML, served by Vercel's Python serverless runtime.
>
> **Why direct Google OAuth over Supabase Auth:** Gmail access requires Google's offline OAuth
> tokens regardless. Doing Google OAuth directly yields both login and Gmail access in one flow,
> so Supabase is used purely as the Postgres database — one auth system instead of two.
>
> **Serverless caveat:** Vercel Python functions are stateless and short-lived (~10–60s). Fine for
> request/response and the manual-sync button. Phase 2 scheduled sync uses Vercel Cron hitting an
> endpoint, not a long-running poller. If sync outgrows the function time limit, fall back to a
> dedicated host (Render/Railway/Fly) — not expected in Phase 1–2.

## 2. System architecture

```
Browser (HTMX)
   │  HTML over AJAX
   ▼
FastAPI on Vercel ──────────────┐
   ├─ /auth/google  (OAuth login + Gmail consent)
   ├─ /sync         (pull + classify, manual in P1 / cron in P2)
   ├─ /dashboard    (renders board)
   ├─ /app/{id}     (detail + email list)
   └─ /app/{id}/edit (manual corrections)
        │                         │
        ▼                         ▼
   Gmail API                Gemini (Classifier adapter)
        │                         │
        └──────────┬──────────────┘
                   ▼
           Supabase Postgres
        (gmail_accounts, applications, emails)
```

## 3. Repository structure

```
apptrack/
├── api/
│   └── index.py            # Vercel entrypoint: `app = FastAPI()` (ASGI)
├── app/
│   ├── main.py             # FastAPI app factory, routes registration
│   ├── config.py           # env loading, settings
│   ├── db.py               # Supabase/Postgres client (asyncpg or supabase-py)
│   ├── auth/
│   │   ├── google_oauth.py # Authlib flow, token exchange/refresh
│   │   └── session.py      # signed cookie helpers
│   ├── gmail/
│   │   ├── client.py       # list/get messages, History API
│   │   └── ingest.py       # fetch candidates → store raw
│   ├── classify/
│   │   ├── prefilter.py    # keyword/sender heuristics
│   │   ├── base.py         # Classifier interface + JSON schema
│   │   └── gemini.py       # Gemini implementation
│   ├── domain/
│   │   ├── stages.py       # enum + ordering + furthest-wins + ghosted calc
│   │   └── dedup.py        # company/role matching
│   ├── routes/
│   │   ├── dashboard.py
│   │   ├── applications.py
│   │   └── sync.py
│   ├── templates/          # Jinja2: base.html, dashboard.html, partials/
│   └── static/
├── migrations/             # SQL files (numbered)
├── tests/
├── requirements.txt
├── vercel.json             # routes all paths to api/index.py
├── .env.example
└── DESIGN.md               # this document
```

`vercel.json`:

```json
{
  "rewrites": [{ "source": "/(.*)", "destination": "/api/index" }]
}
```

## 4. Environment variables

```
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://<app>.vercel.app/auth/google/callback
SUPABASE_DB_URL=               # Postgres connection string
SESSION_SECRET=                # signs cookies
TOKEN_ENCRYPTION_KEY=          # Fernet key for refresh tokens
GEMINI_API_KEY=
CRON_SECRET=                   # Phase 2: protects /sync cron endpoint
GHOSTED_AFTER_DAYS=21          # default, overridable per user
```

## 5. Data model (all phases)

```sql
create type app_stage as enum
  ('referral','applied','screening','assessment','interview','offer','rejected');

create table gmail_accounts (
  user_id          uuid primary key default gen_random_uuid(),
  google_sub       text unique not null,     -- Google account id
  email            text not null,
  refresh_token    text not null,            -- Fernet-encrypted
  last_history_id  text,                      -- Phase 2 incremental sync
  ghosted_after_days int default 21,
  created_at       timestamptz default now()
);

create table applications (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references gmail_accounts(user_id) on delete cascade,
  company       text not null,
  role          text,
  current_stage app_stage not null default 'applied',
  via_referral  boolean default false,
  first_seen    timestamptz not null,
  last_updated  timestamptz not null,
  notes         text,
  manual_override boolean default false,      -- protects user edits from LLM overwrite
  unique (user_id, company, role)
);

create table emails (
  id               uuid primary key default gen_random_uuid(),
  application_id   uuid not null references applications(id) on delete cascade,
  gmail_message_id text not null unique,
  subject          text,
  from_addr        text,
  received_at      timestamptz,
  detected_stage   app_stage,
  confidence       real,
  needs_review     boolean default false      -- low-confidence flag
);

create index on applications(user_id, current_stage);
create index on emails(application_id, received_at);
```

**Phase 3 adds Row-Level Security:**

```sql
alter table applications enable row level security;
create policy own_rows on applications
  using (user_id = current_setting('app.user_id')::uuid);
-- (same for emails via join, and gmail_accounts)
```

## 6. Auth & Gmail OAuth flow

1. User clicks **Sign in with Google** → `GET /auth/google` → redirect to Google consent with
  scopes: `openid email profile https://www.googleapis.com/auth/gmail.readonly`
   and `access_type=offline`, `prompt=consent` (to guarantee a refresh token).
2. `GET /auth/google/callback?code=...` → exchange code for `{access_token, refresh_token, id_token}`.
3. From `id_token` read `sub` + `email`. Upsert `gmail_accounts`; **encrypt** the refresh token
  with Fernet before storing.
4. Set a signed session cookie containing `user_id`.
5. On each Gmail call, use refresh token to mint a short-lived access token (cache in memory for
  the request).

## 7. Gmail ingestion

- **Phase 1 (manual):** `users.messages.list` with query `newer_than:90d` (configurable), page
through IDs.
- **Phase 2 (incremental):** store `last_history_id`; use `users.history.list?startHistoryId=...`
to fetch only new messages.
- For each new message: `users.messages.get` with `format=metadata` (headers: From, Subject, Date)
**+ snippet**. Only fetch `format=full` body when the snippet is ambiguous (saves quota + LLM
tokens).
- Insert into a transient candidates set; dedup against existing `emails.gmail_message_id`.

## 8. Classification pipeline

**Pass 1 — prefilter (`prefilter.py`, free):** keep an email if any:

- sender domain ∈ known ATS list (`greenhouse.io`, `lever.co`, `myworkday.com`, `ashbyhq.com`,
`icims.com`, `smartrecruiters.com`, …) or contains `careers@`, `recruiting@`, `talent@`,
`no-reply@…jobs`;
- subject/snippet matches job regexes (`appl(y|ied|ication)`, `interview`,
`assessment|coding (test|challenge)`, `thank you for your (interest|application)`,
`unfortunately`, `offer`, `referr`).
- Everything else is discarded (not stored).

**Pass 2 — LLM extraction (`gemini.py`):** for survivors, one call, **strict JSON** (use Gemini's
JSON response mode / schema):

```json
{
  "is_job_related": true,
  "company": "string",
  "role": "string|null",
  "stage": "referral|applied|screening|assessment|interview|offer|rejected",
  "via_referral": false,
  "confidence": 0.0
}
```

**Prompt contract (system):**

- "You classify a single email related to a job application. Output ONLY JSON matching the schema."
- "`stage` must be exactly one of the seven enum values. NEVER output 'ghosted' — that is computed
elsewhere."
- "If not job-related, set `is_job_related=false` and leave other fields null."
- "`confidence` < 0.5 means you are unsure."

**Apply rules (`stages.py`):**

- `is_job_related=false` → drop.
- `confidence < 0.5` → store email with `needs_review=true`, do **not** advance the application
stage.
- Else upsert application (dedup by `(user_id, company, role)`), set
`current_stage = max(existing, detected)` by enum order, OR the `via_referral` flag, bump
`last_updated` to email date. **Skip stage advancement if `manual_override=true`.**

## 9. Stage logic

```python
STAGE_ORDER = ['referral','applied','screening','assessment','interview','offer','rejected']

def furthest(a, b):
    # 'rejected' is terminal and always wins if present
    if 'rejected' in (a, b): return 'rejected'
    return max(a, b, key=STAGE_ORDER.index)

def is_ghosted(app, ghosted_after_days):
    return (app.current_stage not in ('offer','rejected')
            and (now() - app.last_updated).days > ghosted_after_days)
```

`ghosted` is **never stored or LLM-assigned** — purely derived at render time. `referral` is both
an entry stage and the `via_referral` flag.

## 10. UI (HTMX pages)


| Route                  | Returns                              | Notes                                                 |
| ---------------------- | ------------------------------------ | ----------------------------------------------------- |
| `GET /`                | landing / sign-in                    |                                                       |
| `GET /dashboard`       | full board page                      | columns or grouped list by stage; ghosted shown muted |
| `POST /sync`           | HTMX fragment: updated board + toast | manual trigger in P1; shows last-synced time          |
| `GET /app/{id}`        | detail panel (HTMX swap)             | emails in time order; each links to Gmail             |
| `GET /app/{id}/edit`   | edit form fragment                   | company/role/stage/referral/notes                     |
| `POST /app/{id}`       | saves + sets `manual_override=true`  |                                                       |
| `POST /app/{id}/merge` | merge duplicate applications         |                                                       |


HTMX patterns: `hx-post="/sync" hx-target="#board" hx-indicator="#spinner"`; detail panel via
`hx-get` into a side drawer.

## 11. Deep-linking to Gmail

Each email row:
`<a href="https://mail.google.com/mail/u/0/#all/{{ gmail_message_id }}" target="_blank">`.
Opens the exact message in the user's Gmail web client. (For multiple signed-in Google accounts,
`u/0` may need to be the right index — Phase 3 can store the account index.)

## 12. Background sync (Phase 2)

- `vercel.json` cron: `{"crons":[{"path":"/sync/cron","schedule":"0 * * * *"}]}` (hourly).
- `/sync/cron` checks `Authorization: Bearer $CRON_SECRET`, then iterates users, runs incremental
History-API sync + classify.
- Persist `last_history_id` after each successful run. Handle `404 historyId too old` by falling
back to a full `messages.list` resync.

## 13. Multi-tenant & security (Phase 3)

- **RLS** on all user tables; set `app.user_id` per request from the session.
- **Token encryption** already in place (Fernet) — rotate keys via envelope encryption if needed.
- **Switch LLM to a no-training tier** (Gemini paid or Claude Haiku) — flip the adapter + key.
- **Privacy policy + data deletion endpoint** (`DELETE /account` purges all rows + revokes Google
token).
- **Google OAuth verification + CASA security assessment** for public `gmail.readonly` use.
Requires homepage, privacy policy, demo video, and (for restricted scopes) an annual third-party
assessment that **costs money and takes weeks**. Plan budget/time before public launch. Until
then, run in **Testing** mode (≤100 allowlisted users).
- Rate-limit `/sync`; add per-user LLM spend caps.

## 14. Phased roadmap & definitions of done

**Phase 1 — Single-user vertical slice**

- Scaffold FastAPI + Jinja + HTMX, deploy hello-world to Vercel
- Supabase project + run migration `001_init.sql`
- Google OAuth login + Gmail consent, encrypted token storage
- Manual `/sync`: list → prefilter → Gemini → upsert
- Dashboard board + detail panel + Gmail deep-links
- Manual edit / merge with `manual_override`
- ✅ **DoD:** you sign in, click Sync, see your real applications with correct-enough stages, and
click through to the actual emails.

**Phase 2 — Automate & polish**

- History-API incremental sync + `last_history_id`
- Vercel Cron hourly `/sync/cron` with `CRON_SECRET`
- Ghosted overlay, stage timeline, last-synced indicator
- `needs_review` queue UI for low-confidence emails
- ✅ **DoD:** data refreshes automatically; corrections are easy; ghosted surfaces itself.

**Phase 3 — Multi-tenant product**

- RLS policies + per-request user context
- No-training LLM tier
- Account deletion + privacy policy + onboarding for new users
- Google OAuth verification / CASA (budgeted)
- ✅ **DoD:** any allowlisted (then verified) user can sign up and use it safely in isolation.

## 15. Local dev & deploy

```bash
# local
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill values
uvicorn app.main:app --reload

# deploy
vercel link && vercel env pull
vercel --prod
```

## 16. Google Cloud setup checklist (human-only)

1. console.cloud.google.com → **New Project** "AppTrack".
2. **APIs & Services → Library → enable Gmail API**.
3. **OAuth consent screen** → External → **Testing** → add your email as a test user → add scopes
  `…/auth/gmail.readonly`, `openid`, `email`, `profile`.
4. **Credentials → Create OAuth client ID → Web application** → add authorized redirect URI
  `https://<app>.vercel.app/auth/google/callback` (and `http://localhost:8000/...` for dev).
5. Copy **Client ID + Secret** → Vercel env + local `.env`.

## 17. Risks register


| Risk                                    | Mitigation                                              |
| --------------------------------------- | ------------------------------------------------------- |
| Google verification wall for public use | Stay in Testing mode; budget CASA before public launch  |
| Free Gemini trains on data              | Paid/no-training tier before multi-user (Phase 3)       |
| Classifier errors                       | Confidence threshold + `needs_review` + manual override |
| Vercel function timeout on large sync   | Batch/paginate; fall back to dedicated host if needed   |
| Dedup mistakes (same co., many roles)   | `(user_id, company, role)` key + manual merge           |
| Refresh-token leakage                   | Fernet encryption at rest; deletion endpoint            |


