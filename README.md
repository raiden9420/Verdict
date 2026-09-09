# Verdict — Evidence-led Research Review

Verdict stress-tests research papers through an evidence-grounded debate. For
each selected review topic, an Attacker critiques the manuscript, a Defender
responds from the paper itself, a deterministic Validator checks every cited
chunk, and a Referee records a ruling and its rationale. Three exchanges form
one round; each round receives its own Debrief Card, and all selected rounds are
then synthesized into a paper-level report.

Phase 3 adds authenticated accounts, database-enforced ownership, configurable
multi-topic audits, domain-aware review standards, author/reviewer report modes,
Markdown export, and comparison across paper revisions. The trust-critical
inner debate cycle remains unchanged.

## September 2026 product overhaul

The workspace now leads with findings and their source passages. Open a finding
for the challenge, defense, ruling, and recorded model provenance. Topic counts
are not a paper score. Model confidence is explicitly uncalibrated; a topical
citation match does not prove support for a particular in-text claim.

- A public `/example` shows a clearly fictional manuscript and three worked
  findings without an account or model call.
- Reuse prepared papers from the searchable library; navigation has URL state,
  browser back/forward, and saved-audit recovery.
- Email/password sign-in includes password reset/recovery. Add the exact local
  or deployed frontend URL to Supabase's allowed redirects for recovery links.
- Export the saved report as Markdown, print/save it as PDF through the browser,
  or export the loaded review record as JSON. JSON includes the current scope,
  findings and recorded provenance; it is not a cryptographically signed record.
- New final reports append a deterministic finding register with stored rulings
  and owned source references. Existing saved reports are not regenerated.
- Source URLs renew before expiry. Completed-topic cards remain accessible when
  a later topic fails. Invalid live/saved artifact payloads enter recovery rather
  than reaching the renderer.
- Bibliography entries must match extracted source text. Topic-specific retrieval,
  duplicate-challenge rejection, and bounded source context reduce avoidable
  repetition and unsupported omission claims.
- `/ready` now checks every product table, `papers.reference_list` (migration
  005), and the private bucket. Successful checks expire after 30 seconds.

No migration was added for this overhaul: provenance and adjudication metadata
use the existing turn-content JSON. Deployments still require the separate
database and environment checks documented below.

## Review locally without credentials

Dependencies are installed in this working copy. From the repository root,
`./scripts/preview-local.sh` also works when Node is only available through the
Codex Desktop runtime. With Node on your PATH, from `frontend/` run:

```bash
npm run preview
```

Open `http://127.0.0.1:3000` and sign in with **preview@example.test** /
**preview-only**. The script starts a loopback-only, in-memory API on port 8765
and Next on port 3000, overriding all public service URLs. No Supabase, model,
or literature service is called. Stop with Ctrl-C; synthetic records reset.
This harness demonstrates UI/transport behavior, not model quality or RLS.
Do not deploy `scripts/preview_server.py` or use real documents in this harness.
The same illustrative findings are reused when testing multiple topics.

On a fresh checkout, first create `backend/.venv`, install
`backend/requirements.txt`, and run `npm ci` in `frontend/`. For actual reviews,
follow the separate backend/frontend setup below with a dedicated development
Supabase project and real provider keys. **Do not start a development backend
against production while an audit is running:** startup recovery assumes one
backend process owns all active work.

## Local verification

```bash
cd backend
.venv/bin/python -m compileall -q app tests ../scripts
.venv/bin/python -m unittest discover -s tests -v
cd ../frontend
npm test
npm run lint
npm run build
```

A production build requires explicit `NEXT_PUBLIC_API_URL`,
`NEXT_PUBLIC_SUPABASE_URL`, and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`; there is
no fallback to the deployed Verdict backend. Use public values only.

The local gate on September 9 ran 206 backend tests (205 passed, one opt-in
live RLS test skipped), 23 frontend tests, TypeScript, lint, and a production
build. Local browser checks used synthetic data, including streamed review,
paper reuse, exports, page navigation, back/forward, reload recovery and phone
layouts. Live model quality, email delivery, real RLS and production quotas
still need a separate integration evaluation. Python 3.12 and Node 24 were used
locally; CI retains Python 3.12.11 and Node 20.x.

## Before paid, unattended use

The useful distinction is a durable, inspectable sequence of challenges,
paper-grounded responses and revision decisions. Multiple roles may still use
the same model; the architecture alone does not demonstrate better scientific
judgment than a strong single-model review.

Prioritize a blinded research-paper benchmark against that baseline, with
expert ratings of false accusations, missed issues, actionable findings and
source support. Then add per-account quotas and cost accounting, durable worker
ownership/recovery, deletion/retention controls and tested billing entitlements.
Calibration and claim-to-cited-source accuracy remain open work. The current
single-process executor must not be horizontally scaled unchanged. This local
version is intended for supervised pilot testing, not an unattended paid launch.

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
- Three scrutiny levels: Constructive, Standard, and Exacting.
- Three depth levels. Depth controls topic breadth—not exchanges per round:
  - Focused (`fast`): 1–2 topics
  - Extended (`deep`): 3–4 topics
  - Full scope (`exhaustive`): 5–6 topics
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
  experimental topics, title/relevance checks, statistical rigor, multi-provider
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

- Python 3.12.11 (the CI/deployment target)
- Node.js 20.x (the CI/deployment target)
- A Supabase project with Auth, Postgres, Storage, and pgvector
- A Gemini API key
- A Groq API key for bibliography extraction (and provider fallback)
- An optional OpenRouter key for provider fallback

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
python -m venv .venv
source .venv/bin/activate
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

# Bibliography extraction and optional LLM fallback
GROQ_API_KEY=

# Optional Phase 2 fallback/integrations
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
npm ci
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

### Citation critiques

For `novelty_scope` and `experimental_setup`, `citation_integrity` critiques
select a specific entry from the paper's extracted reference list and check that
the cited work exists and is topically relevant. `missing_baseline` critiques
instead search for relevant uncited prior art, exclude fuzzy title matches
already present in the extracted reference list, and apply the same existence
and relevance checks to the remaining candidate.

Reference extraction runs once during upload. If no references section is
detected, the upload and audit still proceed normally: `reference_list` is
empty, `citation_integrity` is unavailable, and `missing_baseline` remains
available without bibliography filtering.

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

- PDF report export (Markdown is the required export for this phase)
- Public calibration/benchmark page
- Institutional API

The remaining items are outside the current implementation scope.
