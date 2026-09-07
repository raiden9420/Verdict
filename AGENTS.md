# Verdict repository guide for Codex

This root `AGENTS.md` is the durable handoff that Codex discovers automatically when a session starts in this repository. Read it before changing code. It describes the implemented product, not just the original design. For a narrower explanation of setup and use, see `README.md`. Treat source code, migrations, and tests as authoritative when this file or a planning document drifts.

## Current baseline

- Product: Verdict, an authenticated web application that stress-tests academic papers with an evidence-grounded multi-agent debate.
- Current release identity: Phase 3 / API version `0.3.0`.
- Baseline branch and commit when this guide was written: `main` at `686eba3cdae3c8d145789691033c8949c1ee7f64` (`feat: overhaul citation integrity validation`, 2026-08-31).
- `main` and `origin/main` matched at that baseline. Always run `git status --short --branch` and inspect newer commits before assuming this snapshot is still current.
- Verification on 2026-09-07: 169 backend tests passed; the explicitly opt-in live RLS test was skipped; frontend lint and the production build passed. This local run used the existing Python 3.14/Node 24 installations; Python 3.12.11 and Node 20.x remain the canonical CI/deployment targets.
- A pre-existing untracked `Verdict.code-workspace` was present when this guide was created. Preserve user-owned/unrelated working-tree changes; do not silently add, rewrite, or delete them as part of a bug fix.

Documentation authority:

1. Running code, SQL migrations, and tests.
2. This root guide and `README.md` for the current product and operations.
3. `System Specs.md` for rationale, phased history, tracked gaps, and the latest citation-overhaul acceptance criteria (especially sections 18, 21, and 22). Its early Phase 1 wording is historical and is not the current implementation boundary.
4. `walkthroughs/` for historical Phase 1/2 notes only.

`frontend/README.md` is untouched create-next-app boilerplate and is stale. `frontend/CLAUDE.md` only points to the nested `frontend/AGENTS.md`.

## What the app does

Verdict lets a signed-in user upload a text-based academic PDF, select review dimensions and scrutiny settings, watch a live adversarial audit, inspect grounded evidence and one verdict per exchange, receive one Debrief Card per topic, obtain a paper-level Markdown report, and compare audited revisions of the same paper.

The core trust model is intentionally not a one-pass LLM review:

```text
PDF -> parse/classify/index -> selected topics (sequential)
    -> Attacker -> deterministic Attacker validation
    -> Defender -> deterministic Defender validation
    -> Referee
       repeated exactly 3 times per topic
    -> topic Debrief Card
all topics -> paper-level final report -> optional revision diffs
```

The three verdicts are:

- `SOLIDIFIED`: the defense is supported by valid paper evidence.
- `ACTIONABLE_FLAW`: the Defender concedes, lacks valid evidence, or a cited reference cannot be found.
- `CONTESTED`: the evidence is only partly responsive, the point is a judgment call, low-confidence reruns disagree, or an existing citation appears topically unrelated and therefore needs human review.

The application assists human review; it does not establish scientific truth. In particular, the current citation feature verifies title existence and broad topical relevance, not whether a specific in-text claim accurately characterizes the cited work.

## User-visible lifecycle

### 1. Authentication and private workspace

- The only implemented login provider is Supabase email/password. Signup supports both immediate sessions and confirmation-first projects.
- The browser Supabase SDK persists and refreshes the session. Every protected backend request carries the current access token as a Bearer header; a 401 causes one refresh-and-retry, then a local sign-out.
- All product data is scoped to the Supabase user. Signing into the same account against the same Supabase project makes stored papers, audits, reports, and revisions available on another device through the Paper Library.
- Browser state does not transfer between devices. The theme, Supabase browser session, and `verdict_active_audit:<user-id>` localStorage pointer must be recreated. The durable audit data remains in Supabase, so reopen it from the Library after signing in.

### 2. Paper preparation

- The frontend accepts only PDFs up to 20 MB. The backend is authoritative and also rejects empty, malformed, encrypted, over-20-MB, over-40-page, and image-only/scanned PDFs. OCR is not implemented.
- The backend extracts text with PyMuPDF, then samples the beginning, middle, and end for a strict research-paper classifier. Classification also returns one detected domain: `ml_cs`, `life_sciences`, `social_science`, or `other`.
- A confident non-research result returns a structured 400 that the UI can explicitly override with `force=true`. A classifier outage returns 503 and cannot be overridden.
- Once admitted, the backend extracts the bibliography, creates overlapping ~400-word chunks (50-word overlap), embeds them with Gemini into 384 dimensions, scans domain-specific reproducibility signals, uploads the PDF to private Supabase Storage, and persists the paper/chunks. Partial persistence is compensated with rollback.
- Storage objects are named `<user UUID>/<paper UUID>.pdf`. PDF viewing uses a five-minute signed URL; the service-role key never reaches the browser.

### 3. Audit configuration

There are six fixed topic slugs, mirrored in backend constants and frontend types:

- `novelty_scope`
- `theoretical_soundness`
- `experimental_setup`
- `reproducibility`
- `limitations_impact`
- `statistical_rigor`

Depth controls topic breadth, never exchanges per topic:

| Depth | Allowed topics | Current defaults |
|---|---:|---|
| `fast` | 1-2 | theoretical soundness, experimental setup |
| `deep` | 3-4 | novelty, theory, experiments, reproducibility |
| `exhaustive` | 5-6 | all six topics |

Strictness is `constructive`, `standard`, or `brutal`. Report mode is `author` or `reviewer_assist`. Domain may use the upload-time detection (`auto`) or an explicit override. Strictness and domain change Attacker framing; report mode changes only the final synthesis, not the underlying debate.

### 4. Audit execution and recovery

- `POST /audits` validates ownership, topic counts, optional comparison lineage, and that chunks exist. It creates one audit plus ordered round rows and returns immediately.
- A single background executor runs topics sequentially and admits at most four running/queued audits per process. Each topic runs exactly three accepted Attacker/Defender/Validator/Referee exchanges, then creates one Debrief Card.
- Rejected Attacker attempts are neither streamed nor stored. The initial attempt plus two retries are allowed; exhausting all three fails the audit rather than fabricating a result.
- The Referee is LLM-authored but deterministic guards override unsafe rulings. A Defender concession or invalid/missing defense evidence always becomes `ACTIONABLE_FLAW`.
- A Referee result below 0.5 confidence is rerun once on the same provider. Disagreement forces `CONTESTED`.
- SSE provides low-latency updates, event replay via `Last-Event-ID`, and heartbeats. The browser first reconciles a durable database snapshot, falls back to non-overlapping polling every two seconds when SSE fails, and attempts SSE reconnection every five seconds. Treat `/audits/{id}/turns` and stored artifacts as the source of truth; SSE is an optimization.
- The UI has a single Next.js route (`/`) with four authenticated views: Configure Audit, Audit Results, Final Report, and Paper Library. The live arena has topic tabs, a signed-PDF pane, a filtered Attacker/Defender/Referee transcript, cited-page shortcuts, exchange verdicts, and per-topic debriefs.

Operational constraint: worker queues, event hubs, recovery, and the LLM lock are process-local. The intended deployment is one backend process/replica. Starting another backend against the same production database can mark every existing `in_progress` audit as orphaned during startup. Do not run multiple replicas or point a development backend at production while a live audit is running without redesigning distributed ownership/recovery.

### 5. Reports and revisions

- Final synthesis starts only after every selected round is completed and has exactly one Debrief Card. It is generated once and stored as Markdown.
- Author mode renders Overall Assessment, Preserved Strengths, Priority Revisions, Open Judgment Calls, and Revision Plan.
- Reviewer Assist renders Strengths, Weaknesses, Questions for Authors, and Recommendation.
- Downloading Markdown reads the stored report; it does not invoke an LLM. PDF report export is not implemented.
- A revision upload can start from any owned family member, but the backend links it to the root and safely allocates the next version number. Comparison audits must be completed, belong to an earlier version of the same family, and share at least one topic.
- Version diffs compare stored verdict sets per shared topic and render Resolved Issues, Still Open, New Issues, and Summary. They are LLM summaries, not deterministic claim matching. Rows are idempotent per old audit/new audit/topic.
- The core audit is committed as completed before optional comparison generation. A diff failure must not turn a successful audit into an error. The Final Report view can retry only missing diffs without rerunning the debate.

## Latest feature: citation-integrity overhaul

Commit `686eba3` is the most recent implementation and the first place to look for regressions. It added migration `005_citation_system.sql`, ingestion-time bibliography extraction, `citation_integrity`, a narrowed `missing_baseline` path, unified existence/relevance validation, strict schemas, acceptance tests, and citation status rendering.

### Bibliography extraction

- `backend/app/services/reference_service.py` locates a standalone `References`, `Bibliography`, or `Works Cited` heading, avoids an early table-of-contents match, and stops before an appendix or likely next major section.
- It makes exactly one batched Groq request using `openai/gpt-oss-120b`, then derives stable server-owned IDs `ref-1`, `ref-2`, etc. It accepts at most 200 entries and bounds the input reference block at 240,000 characters.
- This is deliberately optional after the relevance gate. No heading, a Groq outage, malformed model output, or provider/token limit degrades to `reference_list=[]`; it must not fail an otherwise valid upload.
- Migration 005 makes `papers.reference_list` a non-null JSON array with default `[]`. Ingestion has a compatibility fallback that drops only this optional column when an older database lacks it.
- Migration 005 backfills existing papers to `[]`; it does not retroactively extract their bibliographies. Re-upload an old paper to exercise the new feature. Neither the paper API response nor the UI exposes reference count/extraction warnings, so direct database inspection or logs are currently required.
- Important diagnostic nuance: `/ready` currently verifies the Phase 3 schema and private bucket, but does not check `papers.reference_list`. A 200 from `/ready` does not prove migration 005 is applied. Verify the column or upload/read a known bibliography when debugging this feature.

### The two citation paths

These paths are available only in `novelty_scope` and `experimental_setup` rounds.

`citation_integrity`:

1. The Attacker receives the paper-owned reference list and must return one exact `cited_reference_id` with an empty model-authored `external_citations` array.
2. The server resolves the metadata from `papers.reference_list`; never trust model-authored title/author/URL data for this path.
3. The validator searches Semantic Scholar, arXiv, and OpenAlex by title. A fuzzy title match proves existence; broad embedding similarity against up to 8,000 characters of the retrieved paper context checks topical relevance at the named 0.45 threshold.
4. Completed results `verified`, `citation_not_found`, and `topically_unrelated` are findings that must reach the Defender/Referee even when `valid=false`. Unknown reference IDs or unavailable existence/relevance checks are invalid Attacker evidence and trigger regeneration.
5. After acceptance, `attacker_validator_node` materializes canonical, server-owned citation metadata into the persisted/streamed Attacker turn so the frontend can render it.

`missing_baseline`:

1. External candidates are gathered in parallel from the same three services using a query based on retrieved paper text.
2. Candidates fuzzy-matching any extracted reference title (threshold 0.90) are removed before prompting. Novelty candidates are embedding-ranked and limited to the top three.
3. The Attacker must copy a supplied candidate. The server canonicalizes by normalized title plus source; invented or altered candidates fail with `candidate_not_supplied`.
4. The candidate must exist and be topically relevant (`valid=true`) to be accepted. Unlike `citation_integrity`, a false result means the Attacker's evidence is invalid, not that the paper has a bad citation.

### Citation result invariants

- LLM output contracts live in `backend/app/agents/schemas.py` and use strict Pydantic validation with extra fields forbidden. HTTP contracts are separate in `backend/app/models/schemas.py`.
- In-document citations must be UUID chunks belonging to the current paper. Grounding uses a batched Gemini embedding check at 0.60, with a conservative lexical fallback at 0.45 if embeddings fail.
- A complete three-provider negative search is `citation_not_found`. A partial-provider no-match is inconclusive (`existence_check_unavailable`) so a transient outage cannot become a false accusation. A positive title match is conclusive even if only one provider answered.
- An unfindable paper citation deterministically forces `ACTIONABLE_FLAW`. A found but topically unrelated citation cannot be `SOLIDIFIED`; it is capped at `CONTESTED`. Missing-baseline evidence must be fully valid before the debate continues.
- The accepted Attacker turn and the later Validator turn form an implicit frontend contract. `AuditArena.tsx` initially may show Pending, then matches `external_validations` by array index or canonicalized title and maps exact reason strings to Verified, Not found, Off-topic, or Check unavailable. Preserve this ordering/shape or update both sides and add coverage.
- The current UI does not otherwise make meaningful use of fields such as `reference_id`, `citation_type`, `exists`, `relevant`, `matched_title`, and `validation_complete`. Do not remove them casually; they are part of the API/debugging contract and tests.

Focused latest-feature tests:

- `backend/tests/test_reference_service.py`
- `backend/tests/test_citation_overhaul.py`
- `backend/tests/test_literature_search_service.py`
- citation cases in `backend/tests/test_graph_pipeline.py` and `backend/tests/test_backend_pipeline.py`

Known limitations/watch items, not regressions by themselves:

- Full in-text claim-to-source accuracy checking is not built.
- Reference extraction is one Groq call and intentionally fails open to an empty list; very large bibliographies and provider TPM limits need real-world monitoring.
- The 0.45 external relevance threshold has not been calibrated on a labeled cross-domain dataset. Scores near the boundary should not be overstated.
- External relevance uses only the current top-five retrieved chunks (up to 8,000 characters), while identity is primarily a fuzzy title match rather than DOI/author/year confirmation. Both are known false-positive/false-negative risks.
- Completed external-search sweeps are held in an unbounded process cache with no TTL; even a complete empty result remains until restart. Partial sweeps are deliberately not cached.
- `AuditArena.tsx` currently labels the accepted validation `similarity_score` as “Overlap Similarity,” but the citation overhaul repurposed it as embedding relevance between paper context and the matched external work. Treat that wording as a known UI bug, not a backend scoring bug.
- Exhaustive multi-topic audits have not been production load-tested against free-tier quotas.
- There is no frontend unit/component/E2E test suite. Citation UI behavior currently has static typing, lint/build checks, and backend contract tests but should receive regression coverage when fixed or extended.

When debugging the latest feature, inspect in this order:

1. Confirm the deployed commit and whether migration 005 exists; inspect `papers.reference_list` for the uploaded paper.
2. Check backend logs for reference-section detection/structuring degradation before blaming the audit graph. Never log raw secrets or full private paper text.
3. Determine the critique type. `citation_integrity` and `missing_baseline` intentionally interpret `valid=false` differently.
4. Inspect the stored Attacker turn, following Validator turn, and its `external_validations.reason`; do not diagnose from the transient SSE rendering alone.
5. Reproduce with the focused mocked tests. Add a regression test at the narrowest boundary, then run the full backend suite and frontend checks if the contract/UI changed.

## Architecture and code map

### Frontend (`frontend/`)

- Stack: Next.js `16.3.0` App Router, React `19.2.8`, TypeScript 5 strict mode, Porsche Design System React components, Tailwind/PostCSS 4, Inter variable font, Supabase JS, `react-markdown` + GFM.
- `src/app/page.tsx`: authenticated state machine, navigation, upload/launch, recovery, history, and revision setup; `globals.css` owns most layout/theme styling.
- `src/components/AuthProvider.tsx` and `AuthScreen.tsx`: session lifecycle and email/password UI.
- `AuditSetup.tsx`: file/config/revision selection; `AuditArena.tsx`: topic tabs, PDF/transcript, citation badges, verdicts, and debriefs.
- `FinalReportView.tsx`: Markdown/download/diff retry; `LibraryView.tsx`: paper families/history/reopen; `DocumentViewer.tsx`: signed-PDF iframe/page navigation.
- `src/hooks/useSSE.ts`: authenticated stream parsing, replay/reconnect/polling, deduplication, and terminal reconciliation.
- `src/lib/api.ts`: authenticated HTTP/token retry; `audit-config.ts`: options/count rules; `audit-workspace.ts`: recovery and round scoping.
- `src/types/index.ts`: mirror of HTTP and streamed contracts; keep it synchronized with backend schemas/payloads.

Before any frontend edit, read and obey `frontend/AGENTS.md`. This Next.js version has breaking behavior relative to older training knowledge; after installing dependencies, read the relevant guide under `frontend/node_modules/next/dist/docs/` before changing framework code. Do not remove the nested generated instruction block.

### Backend (`backend/`)

- Stack: Python pinned to `3.12.11` for deployment/CI, FastAPI `0.141.1`, Pydantic `2.13.4`, LangGraph `1.2.10`, Supabase `2.31.0`, PyMuPDF `1.28.2`, Google GenAI, pgvector, and authenticated SSE.
- `app/main.py`: lifespan/CORS/recovery/health; `config.py` and `constants.py`: environment and trust thresholds.
- `app/database.py`: anon, JWT/RLS, service-role, and fail-closed context clients; `api/dependencies.py`: token verification.
- `api/papers.py`: upload/index/version/PDF pipeline; `api/audits.py`: jobs, SSE/polling, artifacts, and diff retry.
- `agents/graph.py`: round graph and deterministic guards; `prompts.py`/`schemas.py`: prompt and strict-output contracts; `grounding_validator.py`: deterministic evidence/citation validation.
- `services/llm_client.py`: Gemini -> Groq -> OpenRouter routing, repair/retry, and same-provider referee rerun.
- `embedding_service.py`/`retrieval_service.py`: Gemini vectors, pgvector, lexical fallback; `literature_search_service.py`: external search/cache/filter/ranking; `reference_service.py`: bibliography extraction.
- `relevance_service.py`: Groq/Gemini paper classifier; `reproducibility_service.py`: deterministic signals; `report_service.py`: report/diff generation and storage.
- `migrations/001...005`: apply in numeric order to a new Supabase project. Migration 004 is the destructive pre-launch auth/RLS boundary for anonymous legacy data; do not run migrations blindly against valuable data.
- `tests/`: unit, contract, orchestration, security, migration, report, and latest-feature coverage. Network/LLM work is mocked in the normal suite.

### Deployment and CI

- `render.yaml`: single free Render backend, Python 3.12.11, `/ready` health gate, exact stable frontend origin, and project-scoped Vercel preview regex.
- Frontend deployment is Vercel, but project linkage and Vercel environment settings live outside Git. `frontend/src/lib/api.ts` falls back in production to `https://verdict-backend-dw29.onrender.com`; `NEXT_PUBLIC_API_URL` is not required by the env validator, so set it explicitly to avoid a new deployment silently calling the old backend.
- There is no migration runner in Render or the repository. `render.yaml` does not apply SQL; Supabase migrations are a separate deployment step.
- `.github/workflows/phase3-release-gate.yml`: Python compile + full backend suite and frontend `npm ci`, lint, and build on PRs and pushes to `main`.
- `scripts/verify_phase3_deployment.py`: read-only deployed smoke test for release identity, readiness, OpenAPI/auth, optional CORS/frontend availability, and optional authenticated list access. It does not prove commit `686eba3`, migration 005, or bibliography behavior; `/health` remained version `0.3.0` across the overhaul.

## API surface

Only `GET /health` and `GET /ready` are public application endpoints. FastAPI's generated `/openapi.json`, `/docs`, and `/redoc` remain public; all other application routes require a valid Supabase Bearer access token.

| Method | Route | Behavior |
|---|---|---|
| GET | `/health` | Cheap liveness and version/phase identity; no dependency checks |
| GET | `/ready` | Cached Phase 3 database/private-storage readiness; not a migration-005 proof |
| POST | `/papers?force=&parent_paper_id=` | Validate, classify, extract/index, store, or create a linked revision |
| GET | `/papers` | List owned papers and versions, newest first |
| GET | `/papers/{paper_id}/pdf-url` | Return an owned five-minute signed URL |
| GET | `/papers/{paper_id}/pdf` | Legacy authenticated redirect to the signed URL |
| POST | `/audits` | Validate and enqueue a configured multi-topic audit |
| GET | `/audits` | List owned audit summaries and ordered topic plans |
| GET | `/audits/{audit_id}/stream` | Authenticated replayable SSE; 409 tells clients to poll if an in-progress hub is absent |
| GET | `/audits/{audit_id}/turns` | Durable turns, verdicts, status, and error snapshot |
| GET | `/audits/{audit_id}/debrief` | Legacy first card, only after whole-audit completion |
| GET | `/audits/{audit_id}/debriefs` | Available topic cards, including while later rounds run |
| GET | `/audits/{audit_id}/final-report` | Stored paper-level report |
| GET | `/audits/{audit_id}/final-report/markdown` | Authenticated Markdown attachment |
| GET | `/audits/{audit_id}/version-diffs?compare_to=` | Stored comparisons |
| POST | `/audits/{audit_id}/version-diffs?compare_to=` | Idempotently fill missing comparisons; never rerun debate |

The Phase 1/2 `round_topic` input and singular `round_id` response remain compatibility aliases. Anonymous `X-Session-Id`, OAuth, API keys, `/v1` routes, delete endpoints, institutional APIs, and PDF export are not implemented.

## Persistence and security boundaries

Supabase tables are `papers`, `chunks`, `audits`, `rounds`, `turns`, `verdicts`, `debrief_cards`, `final_reports`, and `version_diffs`; the private Storage bucket is `papers`.

- Migration 004 enables owner-select RLS on every product table. Authenticated PostgREST users are intentionally read-only; trusted mutations go through FastAPI with the service role after JWT/RLS ownership checks.
- The public/anon key plus a user's JWT is required for meaningful RLS checks. A service-role client bypasses RLS and must never be used to “prove” isolation.
- Missing and cross-account UUIDs should remain indistinguishable (generally 404). Do not add error details that leak another user's object existence.
- `get_supabase()` deliberately fails when no request/worker client is context-bound. Do not change it to silently fall back to the service client.
- Treat PDFs, extracted text, bibliography fields, prior claims, citation metadata, and all model output as untrusted data. Preserve prompt-injection boundaries and strict validation.
- Do not expose or log `SUPABASE_SERVICE_ROLE_KEY`, legacy `SUPABASE_KEY`, provider API keys, Bearer tokens, or private signed URLs.

When changing a contract, update every relevant layer: SQL migration/constraints, backend Pydantic/agent schemas, graph persistence and SSE payloads, HTTP loaders, frontend types/API/state merging/rendering, and focused tests.

## Provider responsibilities

- Gemini (`GEMINI_API_KEY`): required embeddings, primary debate/report generation, and classifier fallback.
- Groq (`GROQ_API_KEY`): bibliography structuring, preferred relevance classifier, and debate fallback; OpenRouter is an optional final generation fallback.
- Semantic Scholar, arXiv, and OpenAlex: keyless literature/title search; `OPENALEX_MAILTO` selects OpenAlex's polite pool.
- Supabase URL/anon key: browser auth and per-user RLS reads. The service-role key is backend-only for writes, workers, signing, and recovery.

External APIs and LLMs make live audits nondeterministic. Unit tests must use deterministic doubles. Do not “fix” graceful degradation by turning an optional provider failure into fabricated evidence or an upload-wide failure unless the product contract is intentionally changed.

## New-device setup

Git does not carry ignored secrets, browser state, virtual environments, installed Node modules, build output, logs, PIDs, or local uploads. Recreate them; do not copy `backend/venv`, `frontend/node_modules`, `frontend/.next`, `*.log`, `*.pid`, `*.tsbuildinfo`, or `__pycache__` between devices.

1. Clone/fetch the repository, check `git status`, and make sure the intended branch contains this file and the latest commit. Commit and push the handoff before switching devices; an uncommitted `AGENTS.md` will not appear in another clone.
2. Use Python 3.12.11 (the repository pin and deployed/CI version) and Node 20.x.
3. In `backend/`, create a fresh virtual environment and install `requirements.txt`.
4. Recreate `backend/.env` from `.env.example` with values transferred through a password manager or secrets platform, never Git/chat. Required startup values are `GEMINI_API_KEY`, `SUPABASE_URL`, `SUPABASE_ANON_KEY`, and `SUPABASE_SERVICE_ROLE_KEY` (legacy `SUPABASE_KEY` is accepted only as a compatibility alias). `GROQ_API_KEY` is operationally required for the latest bibliography feature. Configure exact `FRONTEND_ORIGINS`; keep any credentialed origin regex project-scoped.
5. In `frontend/`, run `npm ci`, then recreate `.env.local` from `.env.example`: `NEXT_PUBLIC_API_URL`, `NEXT_PUBLIC_SUPABASE_URL`, and `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`. Every `NEXT_PUBLIC_*` value ships to browsers; never put a service/secret key there.
6. Against a new Supabase project, enable email auth, pgvector, run migrations 001-005 in order, keep the `papers` bucket private, and add the frontend URL to allowed auth redirects. Against the existing project, inspect migration state and apply only missing migrations. Do not replay 001-004 merely because the device changed: migration 004 deletes anonymous legacy rows and rewrites auth/RLS boundaries.
7. Start the backend and frontend in separate terminals:

   ```bash
   cd backend
   source venv/bin/activate
   uvicorn app.main:app --reload --port 8000
   ```

   ```bash
   cd frontend
   npm run dev
   ```

8. Open `http://localhost:3000`, sign in to the same account, and use Paper Library to reopen durable data. A localStorage-selected active audit will not follow the user to the new browser.

## Verification before and after a fix

Normal local gate:

```bash
cd backend
python -m compileall -q app tests ../scripts/verify_phase3_deployment.py
python -m unittest discover -s tests -v
```

```bash
cd frontend
npm run lint
npm run build
```

`npm run build` runs `scripts/validate-env.mjs`, which requires the public Supabase URL/key and rejects a secret/service key. For framework changes, use `npm ci` so the lockfile is authoritative.

The live cross-account RLS test is intentionally opt-in because it contacts Supabase and creates/deletes test records/users. Run it only against an approved test project or dedicated test accounts:

```bash
cd backend
VERDICT_RUN_LIVE_RLS=1 python -m unittest tests.test_auth_rls.LiveCrossAccountRLSTests -v
```

For deployment verification, follow the command and secret-handling notes in `README.md` for `scripts/verify_phase3_deployment.py`. The access token belongs in `VERDICT_SMOKE_ACCESS_TOKEN`, not a command-line argument.

Because that smoke test misses the latest feature, manually verify it after a citation deployment: confirm migration 005, upload a new paper with a standalone References heading, inspect its non-empty `papers.reference_list`, run a novelty or experimental audit, and inspect the persisted Validator turn plus the rendered citation badge.

## Bug-fixing workflow for Codex

- Start by reading the failure report, `git status`, the latest commits/diff, and the closest tests. Reproduce before editing when possible.
- Classify the failure as UI state, SSE transport, durable state, provider degradation, migration drift, auth/RLS, or trust logic. For display issues, reconcile `/turns`, `/debriefs`, `/final-report`, and `/version-diffs` before changing SSE.
- Provider fixes must retain bounded retries, typed validation, partial-provider semantics, and secret-safe errors. Auth/data fixes must preserve two-user isolation, service-role boundaries, scoped CORS, and cross-account 404 hiding.
- Agent fixes must preserve three accepted exchanges, non-persistence of rejected attempts, strict schemas, deterministic citation guards, and paper-only Defender evidence.
- Frontend fixes must preserve focus/ARIA/live status, independent PDF/transcript scrolling, responsive/theme behavior, and authenticated recovery.
- Add a focused regression test for the bug. Run targeted tests while iterating, then the full relevant gates above. If the bug crosses backend/frontend contracts, verify both.
- Avoid unrelated refactors during a trust/security bug fix. Preserve compatibility aliases unless removal is explicitly requested.
- If behavior changes, update this guide, `README.md`, and the tracked-gap/status sections of `System Specs.md` as appropriate. Record the new baseline commit only after it exists; do not pretend an uncommitted tree is a release.
