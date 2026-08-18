# Verdict — Adversarial Research Audits

Verdict stress-tests research papers through an evidence-grounded debate. For
each selected review topic, an Attacker critiques the manuscript, a Defender
responds from the paper itself, a deterministic Validator checks every cited
chunk, and a Referee issues a confidence-scored verdict. Three exchanges form
one round; each round receives its own Debrief Card, and all selected rounds are
then synthesized into a paper-level report.

Phase 3 adds authenticated accounts, database-enforced ownership, configurable
multi-topic audits, domain-aware review standards, author/reviewer report modes,
Markdown export, and comparison across paper revisions. The trust-critical
inner debate cycle remains unchanged.

## Features

- Supabase email/password accounts with refreshed browser sessions.
- Row-Level Security (RLS) on papers, audits, chunks, rounds, turns, verdicts,
  Debrief Cards, final reports, version diffs, and stored PDFs.
- Six fixed topic slugs:
  - `novelty_scope`
  - `theoretical_soundness`
  - `experimental_setup`
  - `reproducibility`
  - `limitations_impact`
  - `statistical_rigor`
- Three strictness levels: Constructive Peer, Standard Reviewer, and Brutal
  Adversary.
- Three depth levels. Depth controls topic breadth—not exchanges per round:
  - Fast: 1–2 topics
  - Deep: 3–4 topics
  - Exhaustive: 5–6 topics
- Domain detection in the existing relevance-classification call, with a user
  override for ML/CS, life sciences, social science, or general research.
- Domain-specific deterministic reproducibility checks, including database
  deposition/material availability and pre-registration/IRB/repository signals.
- Author coaching reports and reviewer-assist drafts with Strengths,
  Weaknesses, Questions for Authors, and Recommendation.
- Authenticated Markdown downloads of the stored final report.
- Linked paper revisions and LLM-summarized, per-topic comparisons of resolved,
  open, and new issues.
- Phase 2 behavior remains intact: external literature grounding for novelty and
  experimental topics, overlap detection, statistical rigor, multi-provider
  fallback, and low-confidence self-consistency checks.

## Architecture

```text
Next.js frontend + Supabase Auth
             │ Bearer JWT
             ▼
FastAPI API + authenticated SSE/polling
             │
             ├── outer audit loop (selected topics, sequential)
             │      └── Attacker → Defender → Validator → Referee × 3
             │          └── one round Debrief Card
             ├── paper-level final-report synthesis
             └── optional prior-version comparison
             │
             ▼
Supabase Postgres/pgvector + private Storage
             └── auth.uid()-scoped RLS policies
```

The backend uses two deliberately separate Supabase credentials:

- A browser-safe anon/publishable key combined with each access token for every
  user-facing read and ownership check. These requests exercise RLS, so another
  account's UUID remains inaccessible even if it is known.
- A backend-only service-role key for writes that follow a successful JWT/RLS
  ownership check, long-running audit workers, and startup reconciliation.
  Authenticated PostgREST clients are read-only; this prevents users from
  fabricating verdicts or reports directly and avoids tying an Exhaustive audit
  to a short-lived browser token. The service-role key must never be exposed to
  the frontend.

## Prerequisites

- Python 3.11+
- Node.js 20+
- A Supabase project with Auth, Postgres, Storage, and pgvector
- A Gemini API key
- Optional Groq and OpenRouter keys for provider fallback

## 1. Supabase setup

1. Create a Supabase project.
2. In **Authentication → Providers**, enable Email. Email/password is the
   required Phase 3 provider. Decide whether email confirmation is required for
   your environment; the frontend handles both immediate and confirmation-first
   signup.
3. In the SQL editor, run every file in `backend/migrations/` in numeric order.
   Migration `004_phase3_product.sql` is the Phase 3 boundary: it discards
   pre-launch anonymous test rows, removes `session_id`, creates the new product
   tables, makes storage private, and enables ownership policies.
4. Copy these values from **Project Settings → API**:
   - Project URL
   - anon/publishable key
   - service-role/secret key
5. For deployed email-confirmation links, add the frontend URL to Supabase's
   allowed redirect URLs.

Do not substitute the service-role key for `SUPABASE_ANON_KEY`. A service-role
client bypasses RLS and would make an apparent ownership test meaningless.

## 2. Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Configure `backend/.env`:

```dotenv
GEMINI_API_KEY=...
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-browser-safe-publishable-key
SUPABASE_SERVICE_ROLE_KEY=your-backend-only-secret-key
FRONTEND_ORIGINS=http://localhost:3000

# Optional Phase 2 fallbacks/integrations
GROQ_API_KEY=
OPENROUTER_API_KEY=
OPENALEX_MAILTO=
```

Then start the API:

```bash
uvicorn app.main:app --reload --port 8000
```

For production, set `FRONTEND_ORIGINS` to a comma-separated list of exact
frontend origins. `render.yaml` already pins the current production Vercel
origin; change it when moving to a custom domain. It also lists every backend
variable used by the deployed service. `FRONTEND_ORIGIN_REGEX` separately
allows only this project's immutable Vercel deployment/preview URLs; do not
replace it with a broad `*.vercel.app` rule when credentials are enabled.

## 3. Frontend

```bash
cd frontend
npm install
cp .env.example .env.local
```

Configure `frontend/.env.local`:

```dotenv
NEXT_PUBLIC_API_URL=http://localhost:8000
NEXT_PUBLIC_SUPABASE_URL=https://your-project.supabase.co
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=your-browser-safe-publishable-key
```

All `NEXT_PUBLIC_*` values ship to the browser. Only the publishable key belongs
there—never the service-role key.

Start the app:

```bash
npm run dev
```

Open [http://localhost:3000](http://localhost:3000), create an account, and sign
in. The browser SDK stores and refreshes the Supabase session. API calls and SSE
reconnections send the current JWT as a Bearer token; tokens are never put in
URLs.

## Using Verdict

### Configure an audit

1. Upload a PDF (maximum 20 MB and 40 pages; scanned/image-only PDFs are
   rejected).
2. Review the detected research domain and optionally override it.
3. Select strictness, depth, and the allowed number of topics for that depth.
4. Choose Author or Reviewer Assist mode.
5. Launch the audit. Each selected topic runs sequentially with exactly three
   exchanges and produces a separate Debrief Card.

Fast is the default. Exhaustive can run six topics and substantially increases
LLM calls, elapsed time, and the likelihood of exhausting free-tier quota.

### Final report and Markdown export

The Final Report becomes available only after every selected round and its
Debrief Card have completed. Reviewer Assist changes only the final synthesis
format; it does not alter the underlying critique, defense, validation, or
verdicts.

Use **Download Markdown** in the final-report view. The frontend fetches the
protected export with its Bearer token and downloads the stored Markdown; it
does not invoke another LLM call.

### Upload a revision

1. From a completed paper/audit, choose **Upload new version**.
2. Upload the revised PDF. It is linked to the owned version family and receives
   the next version number.
3. Audit the same topic set as the comparison audit.
4. After the new audit completes, Verdict compares matching-topic verdict sets
   and stores summaries under Resolved Issues, Still Open, New Issues, and
   Summary.

Version comparison is intentionally an LLM-summarized audit diff. It does not
pretend to deterministically match every differently worded claim across two
independent debate runs. If comparison synthesis alone is interrupted, use
**Retry comparison** in the final-report view; it reuses stored verdicts and
already-completed topic diffs without rerunning the debate.

## API overview

Except for `/health` and `/ready`, all endpoints require
`Authorization: Bearer <supabase-access-token>`.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/papers` | List the user's papers and versions |
| `POST` | `/papers` | Validate, classify, index, and store a PDF |
| `GET` | `/papers/{id}/pdf-url` | Return an owned, short-lived PDF URL |
| `GET` | `/audits` | List the user's audits and topic plans |
| `POST` | `/audits` | Start a configured multi-topic audit |
| `GET` | `/audits/{id}/stream` | Authenticated SSE stream |
| `GET` | `/audits/{id}/turns` | Durable polling/reconciliation snapshot |
| `GET` | `/audits/{id}/debrief` | First Debrief Card (legacy compatibility) |
| `GET` | `/audits/{id}/debriefs` | All topic Debrief Cards |
| `GET` | `/audits/{id}/final-report` | Stored paper-level report |
| `GET` | `/audits/{id}/final-report/markdown` | Markdown attachment |
| `GET` | `/audits/{id}/version-diffs` | Stored revision comparisons |
| `POST` | `/audits/{id}/version-diffs` | Retry missing revision comparisons without rerunning the audit |

Example audit request:

```json
{
  "paper_id": "00000000-0000-4000-8000-000000000001",
  "round_topics": ["theoretical_soundness", "experimental_setup"],
  "strictness_level": "standard",
  "depth": "fast",
  "mode": "author",
  "domain": "auto",
  "compare_to_audit_id": null
}
```

The Phase 1/2 `round_topic` request field and singular `round_id` response field
remain available as compatibility aliases. Anonymous `X-Session-Id` access is
intentionally removed.

## Verification

`GET /health` is a process-liveness check and identifies the running release.
`GET /ready` additionally verifies the Phase 3 tables/columns and private paper
bucket. Render uses `/ready`, so a release with an unapplied migration cannot be
promoted as healthy.

Run backend tests:

```bash
cd backend
python -m unittest discover -s tests -v
```

The suite includes an opt-in live RLS isolation test. After applying migration
004, explicitly enable it to prove through FastAPI, direct PostgREST, and private
Storage that account B cannot query or mutate any of account A's product rows.
If dedicated credentials are omitted, the test creates two confirmed temporary
users through the service admin API and deletes them after the run:

```bash
cd backend
VERDICT_RUN_LIVE_RLS=1 \
python -m unittest tests.test_auth_rls.LiveCrossAccountRLSTests -v
```

For repeat CI runs, provide two dedicated confirmed accounts instead:

```dotenv
VERDICT_RLS_TEST_USER_A_EMAIL=...
VERDICT_RLS_TEST_USER_A_PASSWORD=...
VERDICT_RLS_TEST_USER_B_EMAIL=...
VERDICT_RLS_TEST_USER_B_PASSWORD=...
```

Keep `VERDICT_RUN_LIVE_RLS=1` as an explicit CI secret/value when running the
dedicated-user form. This is a database-layer test, not a UI visibility check.

Frontend checks:

```bash
cd frontend
npm run lint
npm run build
```

The frontend `prebuild` check fails the deployment if its Supabase public URL or
publishable key is absent, or if a Supabase secret key is accidentally supplied.

After deployment, run the read-only release smoke test. It checks the release
identity, readiness, Phase 3 route/auth contract, unauthenticated 401 boundary,
and production CORS. Put an access token in the environment—not a command-line
argument—to include authenticated history checks:

```bash
VERDICT_SMOKE_ACCESS_TOKEN=... \
python scripts/verify_phase3_deployment.py \
  --api-url https://verdict-backend-dw29.onrender.com \
  --frontend-url https://verdict-nu-rouge.vercel.app \
  --require-authenticated
```

GitHub Actions runs the backend suite, Python compilation, migration contracts,
frontend lint, environment validation, and production build on every pull
request and push to `main`.

## Deliberately deferred

- External reference-list extraction/citation overhaul
- PDF report export (Markdown is the required export for this phase)
- Public calibration/benchmark page
- Institutional API

Those items remain Phase 4 or later scope; the existing external-literature
existence validation remains unchanged.
