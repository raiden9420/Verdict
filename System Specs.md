# Autonomous Multi-Agent Adversarial Research Audit System
## Product & Technical Specification (Full Vision, Phase 1 Scoped)

**Status:** Living document. Feature tags `[P1]`–`[P4]` indicate the build phase. Only `[P1]` items are in scope for the current development pass — everything else is here so every phase has full context on where it's headed.

---

## 1. Executive Summary

A web application that audits research papers through a structured, turn-based debate between three AI personas — an **Attacker** (skeptical reviewer), a **Defender** (author advocate grounded strictly in the paper's text), and a **Referee** (deterministic adjudicator) — instead of a single-pass model summary. Every claim that survives or fails the debate is backed by a programmatic citation check, not just an LLM's self-report, which is what separates this from a "paste your paper into ChatGPT" workflow and is the foundation of why a user would trust — and pay for — the verdict.

---

## 2. Problem & Value Proposition

- **Authors** have no reliable, repeatable way to stress-test a paper before submission. Friends and advisors are busy, biased toward kindness, or not deep enough in the exact subfield.
- **Reviewers** are stretched thin and inconsistent — the same paper can get wildly different verdicts depending on who's assigned.
- **Single-pass LLM review tools** exhibit confirmation bias (they tend to agree with whatever framing they're given) and hallucinate citations or "missing baselines" that don't exist.

This system's answer: force the critique and the defense to fight it out, and don't let either side's claims count until they're checked against the actual source text.

| Audience | What they get |
|---|---|
| Authors (pre-submission) | A brutal, private practice reviewer that finds real gaps before a real reviewer does |
| Reviewers / PC members | A structured second opinion that accelerates their own review without replacing their judgment |
| Labs / research groups | A repeatable internal QA pass before a paper goes out the door |

---

## 3. Core Concept

### 3.1 The three personas
- **Attacker (Skeptic):** interrogates methodology, searches for missing baselines or prior art, hunts for mathematical inconsistencies, unstated assumptions, dataset limitations.
- **Defender (Author Advocate):** grounded strictly in the paper's text and supplementary materials; defends the methodology using only what's actually in the document; concedes when it can't.
- **Referee (Judge):** adjudicates each exchange turn-by-turn using a deterministic matrix, and generates round-level and paper-level summaries.

### 3.2 The Fact Solidification Logic Matrix (revised — three verdict states)

The original two-outcome matrix (Defender wins / Attacker wins) is extended with a third state, because not every exchange resolves cleanly — and pretending it does undermines trust in the cases that genuinely are clear-cut.

| Verdict | Condition | Outcome |
|---|---|---|
| **Solidified Fact** | Defender refutes the critique with citations that are verified as real and directly relevant | Locked in as a *Verified Core Strength* |
| **Actionable Flaw** | Defender cannot produce a citation, or the cited passage fails verification | Flagged as a *Critical Actionable Improvement* |
| **Contested** *(new)* | Defender's citation is verified and relevant but only partially addresses the critique, or the critique is a legitimate value judgment rather than a falsifiable gap | Logged as an *Open Judgment Call* — flagged for human review rather than forced into a binary |

Every verdict carries a **confidence score (0–1)**, not just a label — so a user can tell "resolved with high confidence" apart from "resolved, but worth double-checking yourself."

### 3.3 Round topics (fixed list)

The audit runs against a fixed, pre-defined list of round topics rather than an open-ended discussion — each isolates one dimension of academic rigor. This list is fixed across all phases; what changes by phase is how many of them a user can run in one audit (Phase 1: pick exactly one; Phase 3: select any subset, run sequentially).

| # | Topic | Slug | What it probes |
|---|---|---|---|
| 1 | Novelty, Scope & Problem Formulation | `novelty_scope` | Is the problem well-posed, and is the contribution actually novel? |
| 2 | Theoretical Soundness & Mathematical Rigor | `theoretical_soundness` | Are the assumptions, proofs, and derivations sound? |
| 3 | Experimental Setup, Datasets & Baselines | `experimental_setup` | Are the experiments fair, and are the baselines/comparisons appropriate? |
| 4 | Reproducibility, Compute & Ablation Studies | `reproducibility` | Could someone else reproduce this? Are ablations sufficient to support the claims? |
| 5 | Limitations, Broader Impact & Edge Cases | `limitations_impact` | Does the paper honestly address where it breaks down? |

Use the `Slug` column as the actual stored enum value in code — don't let the agent invent its own identifiers.

Each topic gets its own Attacker system-prompt framing (see §7.1) so the critique stays relevant to that dimension rather than drifting into general commentary.

### 3.4 The trust layer: grounding validation

This is the single highest-leverage addition to the original design. Before the Referee adjudicates, a **deterministic, non-LLM validator** checks that every citation the Defender (or Attacker) makes actually exists in the retrieved source chunks and is semantically consistent with the claim being made. The Referee's prompt receives this validation result as part of its context — trust doesn't rely purely on an LLM grading its own homework.

---

## 4. User Personas & Use Cases

1. **Grad student / postdoc, pre-submission** — wants an honest, repeatable stress test before sending a paper to a venue.
2. **Conference/journal reviewer** — wants a structured accelerant for their own review, with citations they can independently verify.
3. **Lab PI or research-integrity reviewer** — wants a QA gate before internal work is shared externally.

---

## 5. Full Feature Set (Complete Vision)

- Multi-agent adversarial debate (Attacker / Defender / Referee) `[P1]`
- Three-state Fact Solidification Matrix (Solidified / Actionable Flaw / Contested) `[P1]`
- Deterministic citation/grounding validator `[P1]`
- Round-based structure: any one round topic, in-document grounding only `[P1]`
- Full 5-round sequence, user-selectable subset `[P3]`
- Strictness levels (Constructive Peer / Standard Reviewer / Brutal Adversary) — Standard only `[P1]`; all three selectable `[P3]`
- Depth control (Fast / Deep / Exhaustive) — Fast only `[P1]`; all three `[P3]`
- Split-screen dashboard: Document Viewer + Live Arena `[P1]`
- Round Debrief Cards (Executive Synthesis, Solidified Strengths, Actionable Weaknesses, Contested Points) `[P1]`
- External literature grounding (Semantic Scholar, arXiv, OpenAlex) for the Attacker `[P2]`
- Dedicated statistical/methodological rigor check (p-values, sample sizes, multiple comparisons) `[P2]`
- Novelty / overlap detection via embedding search against related work `[P2]`
- Reproducibility scoring (code/data/hyperparameters/compute disclosed?) `[P2]`
- Self-consistency re-runs for low-confidence verdicts `[P2]`
- Multi-provider LLM router with fallback (Gemini / Groq / OpenRouter) `[P2]`
- User accounts & auth `[P3]`
- Version tracking — re-audit a revision, diff against the prior audit `[P3]`
- Dual mode: Author practice mode vs. Reviewer-assist draft report `[P3]`
- Domain-calibrated personas (ML vs. wet-lab bio vs. social science standards) `[P3]`
- Exportable formatted review report (PDF/Markdown) `[P3]`
- API access for institutional integration `[P4]`

---

## 6. System Architecture (Full Vision)

```
Frontend (Next.js)
        │
Backend API (FastAPI, SSE streaming)
        │
Agent Orchestration (LangGraph state machine)
   ├── Attacker node
   ├── Defender node
   ├── Grounding validator node   (deterministic, non-LLM)
   └── Referee node
        │
   ┌────┴─────────────────┬─────────────────────┐
Model router          Grounding engine      Literature search
(Gemini / Groq /      (parsing + local      (Semantic Scholar,
 OpenRouter, free)     embeddings + pgvector) arXiv, OpenAlex) [P2]
        │
Data & auth (Supabase Postgres, free tier)
```

**Phase 1 builds:** Frontend, Backend API, Agent Orchestration (Attacker/Defender/Referee + validator), a single-provider model client, the grounding engine (parsing + local embeddings + pgvector), and Postgres persistence. **Deferred:** external literature search, multi-provider routing, accounts/auth.

---

## 7. Agent Design — Detailed Specs

### 7.1 Attacker
- **Goal:** identify the single most significant, relevant weakness for the current round topic that hasn't already been raised this round.
- **Input:** round topic, retrieved paper excerpts, and the `claim_summary` of every prior exchange in this round — feed these explicitly as "already raised, don't repeat" context; don't rely on the model remembering on its own.
- **Constraint:** must cite the excerpt(s) it's responding to when critiquing something present in the text; may raise an "omission" critique (something the paper *should* address but doesn't) without a citation.
- **Trust note:** the Attacker's own citations go through the Grounding Validator (§7.3) exactly like the Defender's. If the Attacker cites a chunk that doesn't actually say what the critique claims, discard the exchange before it reaches the Defender — a fabricated critique should never cost the Defender a turn or count toward a verdict.
- **Output schema:**
```json
{
  "claim_summary": "string, one sentence",
  "critique_text": "string",
  "cited_chunk_ids": ["chunk_id", "..."],
  "critique_type": "omission | inconsistency | unstated_assumption | dataset_limitation"
}
```

### 7.2 Defender
- **Goal:** refute the critique using only the paper's own text, or concede.
- **Constraint:** never invent methodology, results, or justification absent from the source. If no grounding exists, it must concede rather than argue from general knowledge.
- **Output schema:**
```json
{
  "rebuttal_text": "string",
  "cited_chunk_ids": ["chunk_id", "..."],
  "concedes": true
}
```

### 7.3 Grounding Validator (deterministic, not an LLM persona)
- **Input:** a `cited_chunk_id` + the claim being attributed to it. Runs on **every** citation from **both** the Attacker and the Defender, not just the Defender. A turn that cites multiple chunks gets each one validated independently.
- **Logic:** confirm the chunk exists; compute embedding similarity (using the same local model as retrieval, so vectors are directly comparable) between the claim's paraphrase and the actual chunk text; flag `valid: false` below the threshold.
- **Threshold:** fixed at `0.6` cosine similarity for Phase 1 — not user-configurable, but implement it as a named constant, not a magic number, so Phase 2 can tune or expose it.
- **No-citation case:** an Attacker "omission" critique has nothing to validate — skip validation and pass it through. A Defender turn that concedes with no citations likewise skips validation.
- **Output:**
```json
{ "chunk_id": "string", "valid": true, "similarity_score": 0.0 }
```
- This runs automatically on every citation before the Referee sees it. The Referee receives the full set of validation results for the exchange, not a single collapsed flag.

### 7.4 Referee
- **Goal:** adjudicate using the three-state matrix, informed by the validator's output — not just the Defender's say-so.
- **Output schema:**
```json
{
  "verdict": "SOLIDIFIED | ACTIONABLE_FLAW | CONTESTED",
  "confidence": 0.0,
  "rationale": "string"
}
```
- **Decision logic** (assumes the Attacker's own critique already passed grounding validation — per §7.1, if it didn't, the exchange never reaches the Referee at all):
  - Defender cites real, verified, directly-relevant evidence → `SOLIDIFIED`
  - Defender concedes, or citation fails validation → `ACTIONABLE_FLAW`
  - Citation verified but only partially responsive, or critique is a legitimate value judgment → `CONTESTED`

### 7.5 Round mechanics: exchange count & termination

A round is a fixed sequence of exchanges, not an open-ended conversation. **Phase 1 (Fast depth): 3 exchanges per round, hardcoded as a named constant.** Each exchange is one full Attacker → Defender → Validator → Referee cycle. After the 3rd exchange, the round ends automatically and the Debrief Card synthesis (§7.6) runs. Deeper, configurable exchange counts are a `[P3]` concern — don't build that configurability now, just don't bury the number 3 somewhere it's painful to change later.

### 7.6 Debrief Card generation

The Debrief Card is **not** produced inline by the per-exchange Referee — it's a separate, round-level synthesis step that runs once, after all 3 exchanges complete. It takes the full round transcript (every turn and verdict) as input to one dedicated LLM call and produces the four-section output defined in §8. Model this as a fifth node in the LangGraph graph, distinct from the per-exchange Referee node.

---

## 8. Data Model

| Entity | Key fields |
|---|---|
| `Paper` | id, filename, storage_path, parsed_text, uploaded_at |
| `Chunk` | id, paper_id, section, text, embedding (`vector(384)`), page_number |
| `Audit` | id, paper_id, session_id, round_topic (slug from §3.3), strictness_level, depth, status, created_at |
| `Round` | id, audit_id, round_number, topic, status |
| `Turn` | id, round_id, **exchange_number**, agent_type (attacker/defender/referee/validator), sequence, content (JSON), created_at |
| `Verdict` | id, round_id, **exchange_number**, claim_summary, verdict_type, confidence, cited_chunk_ids |
| `DebriefCard` | id, round_id, executive_synthesis, solidified_strengths (JSON list), actionable_weaknesses (JSON list), contested_points (JSON list) |

**Notes:**
- `Chunk.embedding` is a fixed `vector(384)` pgvector column, matching `all-MiniLM-L6-v2`'s output dimension exactly. Set this at table-creation time — changing embedding models later means re-embedding every chunk, not just altering the column.
- `exchange_number` (1, 2, or 3 — see §7.5) is what ties one Attacker turn, its Defender turn, its validator results, and its Referee verdict together as a single exchange. Use it to group turns for both the Live Arena display and the Debrief Card synthesis.
- `Audit.session_id` is a client-generated anonymous UUID, not a user account — see §10.

---

## 9. Technical Stack (Free-Resource Constrained)

| Layer | Choice | Notes |
|---|---|---|
| Frontend | Next.js (App Router, TypeScript) + Tailwind, hosted on Vercel free tier | |
| Backend | FastAPI (Python), hosted on Render/Railway free tier | Free web services sleep on inactivity — accept cold-start latency for now |
| Orchestration | LangGraph (open source) | Self-hosted, no cost |
| LLM (Phase 1) | Google Gemini API (`gemini-2.5-flash` / `flash-lite`), free tier | No credit card required; tight per-minute/per-day caps — see §11 |
| Embeddings | Local, via `sentence-transformers` (`all-MiniLM-L6-v2`, 384-dim), CPU | Free, no API quota consumed — reserves the Gemini free-tier quota for actual debate generation |
| Chunking & retrieval | ~300–500 word chunks, ~50-word overlap, chunked on paragraph boundaries where possible; top-k = 5 per retrieval call | Named constants, not magic numbers — Phase 2/3 may tune these |
| PDF parsing | `pdfplumber` / `PyMuPDF` | GROBID is a stronger upgrade path for Phase 2 (better section-aware parsing) but adds a Java service dependency not worth taking on yet |
| Vector store + app DB | Supabase Postgres + `pgvector`, free tier | One service for both audit data and embeddings — avoids stitching together two systems |
| Streaming | Server-Sent Events (SSE) | Simpler than WebSockets for one-directional agent-turn streaming |
| Multi-provider routing, external literature APIs (Semantic Scholar, arXiv, OpenAlex) | — | Deferred to Phase 2 — build the LLM client behind a simple interface now so a second provider is a config change later, not a rewrite |

---

## 10. UX/UI Design — Phase 1 Scope

- **Upload screen:** drag-and-drop PDF, a round-topic picker (one of the five topics), no other config exposed yet (strictness defaults to Standard Reviewer, depth defaults to Fast). Enforce and surface the PDF constraints from §11 with a clear inline error, not a silent failure.
- **Split-screen workspace:**
  - *Left — Document Viewer:* renders the parsed paper; when a turn cites a chunk, scroll to and highlight that **page**. Page-level only for Phase 1 — precise inline text-span highlighting needs bounding-box data the parser doesn't extract yet; that's a Phase 2 upgrade.
  - *Right — Live Arena:* a streaming, chat-like transcript of Attacker → Defender → Referee turns, each visually distinguished by persona (suggested: Attacker = coral/red, Defender = teal/green, Referee = neutral gray).
- **End-of-round Debrief Card:** Executive Synthesis, Solidified Strengths, Actionable Weaknesses, Contested Points, each as an accordion section.
- No login/account screen in Phase 1. On first visit, the frontend generates a random UUID, stores it in `localStorage`, and sends it as an `X-Session-Id` header on every request. The backend uses this to scope `Audit` rows to the browser that created them — no password, no cross-device continuity.

---

## 11. Non-Functional Requirements

- The system must operate within free-tier LLM rate limits: implement request queuing and exponential backoff on `429` responses rather than naive immediate retries, which burn quota fastest.
- Batch each agent's turn into a single LLM call rather than one call per micro-step, to conserve requests-per-minute budget.
- **Concurrency:** Phase 1 processes one audit's LLM calls at a time — a simple in-process lock or queue is sufficient. If a second audit is requested while one is in flight, queue it rather than firing both sets of calls in parallel and blowing the per-minute cap.
- **PDF limits:** reject uploads over 40 pages or 20MB. Reject any PDF whose extracted text is empty or near-empty (a strong signal it's a scanned/image-only document) with a clear error, rather than silently proceeding with an empty-grounding audit. OCR support is out of scope for Phase 1.
- **SSE resilience:** if the stream connection drops mid-round (e.g. a free-tier cold start), the frontend should fall back to polling the turns and debrief endpoints rather than leaving the user on a silently stalled screen.
- All API keys and secrets via environment variables — never hardcoded or committed.
- Target: a single-round Phase 1 audit should complete in well under 5 minutes of wall-clock time on free-tier infrastructure.

---

## 12. Roadmap Recap

- **Phase 1 — Foundations:** core 3-agent debate loop, in-document grounding + validator, one round topic at a time, split-screen UI, Debrief Card. *(current build)*
- **Phase 2 — Depth:** external literature grounding, statistical rigor round, novelty/overlap check, reproducibility scoring, multi-provider LLM router, self-consistency checks.
- **Phase 3 — Product:** accounts, full round/strictness/depth configurability, version tracking & diffing, dual author/reviewer modes, domain-calibrated personas, exportable reports.
- **Phase 4 — Trust at scale:** institutional API. (A calibration benchmark was originally planned here too; see §18 — eliminated before being built.)

---

## 13. Definition of Done — Phase 1

A user can, without an account:
1. Upload a PDF and pick one round topic.
2. Watch a live, streaming 3-agent debate — a fixed 3 exchanges (§7.5) — in the split-screen UI, with page-level highlighting following each citation.
3. See each exchange resolve into a citation-grounded verdict — including the `Contested` state actually firing on ambiguous exchanges, not just the two binary outcomes.
4. Receive an end-of-round Debrief Card, generated as its own synthesis step (§7.6) after the 3rd exchange.
5. Get an immediate, clear error when uploading an oversized or scanned/image-only PDF, instead of a silent failure.
6. All of the above running end-to-end on the free-tier stack listed in §9, reachable at a public URL.

---

## 14. Phase 2 — Technical Specification

**Prerequisite:** Phase 2 assumes the Phase 1 review fixes are already applied (correct `google-genai` package, a verified Gemini model string, the closed Attacker validation loophole, and PDF storage moved off local disk). Everything below builds on a working Phase 1 — it does not touch the core 4-node debate loop, the fixed 3-exchange round, or the single-topic picker. Those stay exactly as built.

### 14.1 Statistical Rigor round (6th topic)

Add a sixth fixed round topic, extending §3.3's table:

| # | Topic | Slug | What it probes |
|---|---|---|---|
| 6 | Statistical Rigor | `statistical_rigor` | Are p-values, sample sizes, effect sizes, and multiple-comparison corrections handled correctly, and does reported significance actually support the claims made? |

This is a drop-in addition to the existing `ROUND_TOPICS` / `TOPIC_ATTACK_FRAMING` pattern from `constants.py` — no new agent or graph logic required, just a new dict entry plus a matching Attacker framing string and a new option in the frontend's round-topic picker.

### 14.2 External literature grounding

The Attacker gains the ability to search external literature — but only for the two topics where it's actually useful: `novelty_scope` (checking for unstated prior art) and `experimental_setup` (checking for missing standard baselines). The other four topics — including the new `statistical_rigor` — stay in-document-only. This is a deliberate scope boundary, not an oversight.

**Design:** rather than giving the Attacker live tool-calling (extra LLM round-trips, more free-tier rate-limit pressure), use a two-step deterministic-then-generate pattern consistent with how in-document retrieval already works:
1. Build a search query from the round topic + the paper's own abstract/intro chunk (a simple heuristic extraction is enough — don't add a new LLM persona just for this).
2. Execute the search against external APIs deterministically, in the backend, no LLM involved.
3. Inject the formatted results into the Attacker's context alongside the usual in-document chunks, the same way retrieved chunks already are.

**External sources (free, no cost — query in parallel, merge, de-duplicate by title):**
- **Semantic Scholar Graph API** — `GET https://api.semanticscholar.org/graph/v1/paper/search?query={q}&limit={n}&fields=title,abstract,authors,year,url,externalIds`. No key required at Phase 2's usage level (roughly 100 requests/5 min unauthenticated).
- **arXiv API** — `GET http://export.arxiv.org/api/query?search_query=all:{q}&max_results={n}`, Atom XML response — use the `feedparser` package rather than hand-rolling XML parsing.
- **OpenAlex** — `GET https://api.openalex.org/works?search={q}&per-page={n}`. Fully open, no key. OpenAlex stores abstracts as an `abstract_inverted_index` (word→position mapping) rather than plain text — write a small helper to reconstruct plain text from it. Add a `mailto` query param (configurable via env var) to get OpenAlex's better-rate-limited "polite pool."

**Caching:** cache search results in-process (a dict or `functools.lru_cache` keyed by normalized query string is sufficient — no new database table needed).

**Citation type + validation:** external citations can't be checked against a local chunk. Add a new validator function that checks *existence* rather than semantic grounding — confirm the cited external work actually appeared in that search call's results, catching a fabricated title/author. Verifying the external paper's full content is out of scope. Store external citations as a JSON field on the Attacker's turn content (e.g. `external_citations: [{title, authors, year, url, source}]`) — no new table needed, `turns.content` is already JSONB.

### 14.3 Novelty / overlap detection

For the `novelty_scope` topic specifically, after the external search in §14.2 returns candidates, compute embedding similarity (reuse the existing local `sentence-transformers` model) between the paper's own abstract and each candidate's abstract. Surface the top 2–3 most similar external works with their similarity scores to the Attacker — this turns a vague "not novel enough" critique into a specific, checkable one ("this closely overlaps with [X], similarity 0.81").

### 14.4 Reproducibility signals

A new deterministic (non-LLM) service that scans a paper's chunks for concrete disclosure signals: code/repository links, dataset availability statements, hyperparameter disclosure, compute/hardware disclosure, and random-seed disclosure. Run once at paper ingestion time (cheap, no LLM cost) and store the result as a JSONB column on `papers` (`reproducibility_signals`). Surface it to the Attacker as extra context when `reproducibility` is the selected topic, and add it as a structured checklist field on the Debrief Card whenever that topic is audited.

### 14.5 Multi-provider LLM router

Add `GroqClient` and `OpenRouterClient` as additional subclasses of the existing `LLMClient` ABC — both are OpenAI-compatible APIs and can share most request/response handling plus the existing JSON-repair fallback logic. Add a router that tries providers in a fixed order (Gemini → Groq → OpenRouter), falling back only when the current provider's retries are exhausted due to rate-limiting — not for malformed JSON, which keeps retrying within the same provider first. Each provider's API key is a separate optional env var; if unset, the router skips that provider rather than erroring, so providers can be added incrementally.

### 14.6 Self-consistency re-runs

When the Referee returns a verdict with `confidence` below a new `SELF_CONSISTENCY_THRESHOLD` (start at `0.5`), re-run just the Referee adjudication once more with identical inputs. If the second verdict agrees, keep it. If they disagree, force the verdict to `CONTESTED` and note in the rationale that independent adjudications disagreed — that disagreement is itself informative. This only doubles Referee calls for already-uncertain exchanges, not every exchange.

### 14.7 Data model additions

| Change | Details |
|---|---|
| `papers.reproducibility_signals` | New `JSONB` column, populated at ingestion (§14.4) |
| `turns.content` (attacker rows) | May now include an `external_citations` array — no schema change, already JSONB |
| `ROUND_TOPICS` / `TOPIC_ATTACK_FRAMING` | New `statistical_rigor` entry (§14.1) |

No new tables are required for Phase 2.

### 14.8 Non-functional notes

- Identify your app in external API requests where the provider supports it (e.g. OpenAlex's `mailto` param), and respect each API's documented rate limits independently of the existing Gemini rate-limit handling.
- External calls add real wall-clock time on top of Phase 1's budget — relax the "well under 5 minutes" target from §11 to **well under 10 minutes** for a `novelty_scope` or `experimental_setup` audit that includes external search.

---

## 15. Definition of Done — Phase 2

Building on Phase 1 (§13, still required), a user can additionally:
1. Select "Statistical Rigor" as a round topic and get a debate grounded in that framing.
2. Select "Novelty, Scope & Problem Formulation" or "Experimental Setup" and see at least one Attacker critique reference real external literature, rendered distinctly from in-document citations, with the cited work's existence validated rather than just asserted.
3. Select "Reproducibility" and see the reproducibility checklist populated on the Debrief Card.
4. Get a coherent result even if the Gemini free tier is exhausted mid-audit, via automatic fallback to a second configured provider.
5. See at least one `CONTESTED` verdict in testing that resulted from a self-consistency disagreement, if you can trigger one — confirming low-confidence verdicts are actually double-checked, not just labeled.

---

## 16. Phase 3 — Technical Specification

Phase 3 turns the working audit engine into a product: real accounts, the configurability the original concept doc described, and outputs people can actually take with them. The core 4-node debate graph (Attacker → Defender → Validator → Referee) is untouched — Phase 3 adds an outer loop (multiple round topics per audit) and configuration (strictness/domain framing) around it, not a new inner mechanism.

**Explicit non-goals:** the citation system overhaul (reference-list extraction) discussed separately is deliberately deferred — don't fold it in here. Institutional API access is Phase 4 (§19).

### 16.1 Accounts, auth & Row-Level Security

Use **Supabase Auth** directly — it's already part of the same free Supabase project, so this adds no new service. Email/password is sufficient for Phase 3; OAuth providers (Google, GitHub) are a nice-to-have, not required.

- Replace the `X-Session-Id` header / `localStorage` UUID scheme entirely. The frontend uses the Supabase client SDK for signup/login, which handles JWT storage and refresh automatically.
- The backend verifies the Supabase-issued JWT on protected endpoints (Supabase's server SDK can validate/decode it) and derives `user_id` from it — replacing `get_session_id()` with a real `get_current_user()` dependency.
- Add `user_id UUID REFERENCES auth.users(id)` to `papers` and `audits` (see §16.7).
- **Turn on the Row-Level Security that was explicitly deferred in migration 001.** Add policies scoping `papers`, `audits`, and their children to `auth.uid() = user_id` (directly or via a subquery through the parent `audit`/`paper` for tables that don't carry `user_id` themselves). This closes the access-control gap flagged in both the Phase 1 and Phase 2 reviews — ownership stops being bookkeeping and becomes enforced.
- **No migration path for pre-Phase-3 anonymous audits is required.** This is still pre-launch; don't build a "claim your old audits" flow for data that only exists from your own testing.

### 16.2 Full configurability — strictness, depth & multi-topic audits

The `audits` table has carried unused `strictness_level` and `depth` columns since Phase 1 — this is the phase that finally uses them.

- **Strictness** (Constructive Peer / Standard Reviewer / Brutal Adversary): a prompt-framing change only, analogous to `TOPIC_ATTACK_FRAMING`. Add a `STRICTNESS_FRAMING` dict in `constants.py` and thread it into `attacker_system_prompt()` alongside the topic framing. No graph structure changes.
- **Depth** controls how many round topics run in one audit, not how many exchanges within a round — `EXCHANGES_PER_ROUND` stays fixed at 3 for every depth level, deliberately, to keep per-round-topic cost bounded and predictable. Map depth to a topic count: Fast = 1-2, Deep = 3-4, Exhaustive = 5-6 (all of them). Give each depth level a sensible default topic subset (e.g. Fast defaults to Theoretical Soundness + Experimental Setup) that the user can override, rather than forcing manual selection every time.
- **Multi-topic audits:** the `Round` entity was already modeled as separate from `Audit` back in §8 specifically to allow more than one round per audit later — this is that moment. The graph needs an outer loop over selected round topics, each running its own fixed 3-exchange inner loop and producing its own Debrief Card, before handing off to the new paper-level report (§16.4).

### 16.3 Domain-calibrated personas

Auto-detect the paper's field with a user-override option, rather than requiring manual categorization. Extend `relevance_service.py`'s existing classification call to also return a detected domain (`ml_cs` / `life_sciences` / `social_science` / `other`) in the same response — this is a schema extension of a call that already reads the document, not a new LLM call.

- Add a `DOMAIN_FRAMING` dict (same pattern as topic/strictness framing) that adjusts what counts as a legitimate critique per field — e.g. missing ablations matters for ML, not for a pure theory paper; human-subjects concerns (IRB, informed consent) matter for some social science and life-sciences work and never for ML.
- **`reproducibility_service.py` needs the same domain-awareness, not just the Attacker's prompt.** Its current regex signals (GitHub links, batch size, GPU/TPU mentions) are CS/ML-specific — on a biology or social-science paper they'll correctly find nothing, but absence shouldn't be scored the same way. Add domain-specific signal sets: e.g. life sciences — reagent/material availability statements, database deposition (GenBank, PDB); social science — pre-registration, IRB approval, materials repositories (OSF). Route which signal set runs based on the detected domain.

### 16.4 Paper-level final report & dual mode

Distinct from the existing per-round Debrief Card: a new synthesis step that runs once, after all selected round topics complete, aggregating every round's Debrief Card into the "Final report" the frontend nav already has a slot for.

- **Mode** (`author` / `reviewer_assist`) is a framing choice on this final synthesis call, not a change to the underlying debate — the Attacker/Defender/Referee behave identically either way, keeping the trust-critical path untouched. Only the final report's template and tone change: `author` mode produces a coaching-oriented summary; `reviewer_assist` produces a draft formatted as Strengths / Weaknesses / Questions for Authors / Recommendation, ready for a human reviewer to edit.

### 16.5 Exportable reports

Start with **Markdown export** of the paper-level final report — no new dependency, works immediately. Treat PDF export as a stretch goal, not a requirement: PDF-rendering libraries (e.g. `weasyprint`) carry real memory overhead, and this project has already hit a free-tier RAM ceiling once before (the embedding model migration in Phase 2). If PDF is attempted, load-test it on the actual Render free-tier instance before considering it done, the same lesson from that migration.

### 16.6 Version tracking & diffing

Scope this as an **LLM-summarized diff**, not a fully automated claim-matching algorithm — matching critiques worded differently across two independent debate runs is a genuinely hard problem, and a summarized comparison delivers most of the value without it.

- Add `parent_paper_id UUID REFERENCES papers(id)` and `version_number INT DEFAULT 1` to `papers`. Uploading a new version links it to the original.
- To diff, the new audit should cover the same round topic(s) as the audit being compared against. Feed both sets of verdicts (old and new, same topic) to one LLM call and ask for a structured comparison: which flagged issues appear resolved, which are still open, what's new. Store the result rather than re-deriving it on every view.

### 16.7 Data model additions

| Entity | Change |
|---|---|
| `papers` | add `user_id UUID REFERENCES auth.users(id)`, `parent_paper_id UUID REFERENCES papers(id)` (nullable), `version_number INT DEFAULT 1` |
| `audits` | add `user_id UUID REFERENCES auth.users(id)`, `mode TEXT DEFAULT 'author'`, `domain TEXT`; `strictness_level`/`depth` columns already exist from Phase 1, now populated |
| `final_reports` *(new table)* | id, audit_id, mode, content (JSONB or markdown text), created_at — the paper-level aggregate, distinct from `debrief_cards` |
| `version_diffs` *(new table)* | id, audit_id_old, audit_id_new, round_topic, diff_summary (text), created_at |
| RLS policies | on `papers`, `audits`, and children, scoped to `auth.uid()` (§16.1) |

### 16.8 Non-functional notes

- A multi-topic Exhaustive audit (up to 6 round topics × 3 exchanges each) multiplies the call volume concerns already noted in §14.8 — this is the depth level most likely to strain free-tier quota. Don't default new users into it.
- Auth failures (expired/invalid JWT) should return a clean 401, not surface as a generic 500 from a downstream ownership check failing unexpectedly.

---

## 17. Definition of Done — Phase 3

Building on Phases 1 and 2 (§13, §15, still required), a user can additionally:
1. Sign up, log in, and see only their own papers and audits — confirm this by checking that RLS actually blocks cross-account access, not just that the UI happens not to show it.
2. Configure strictness and depth before starting an audit, and run an audit covering more than one round topic in a single pass, with a Debrief Card per topic.
3. Get a domain-appropriate critique — confirm a life-sciences or social-science test paper produces different Attacker framing (and different reproducibility signals) than an ML paper does.
4. Choose reviewer-assist mode and get a final report formatted as a draft review rather than a coaching summary, from the same underlying debate.
5. Download the final report as Markdown.
6. Upload a second version of a previously-audited paper and get a diff summary describing what changed.

---

## 18. Tracked gaps & deferred polish

A running list, not a one-time snapshot — append to this rather than losing findings in chat history. Each entry gets a status: **Open** (known, not yet fixed), **Watching** (a risk flagged in spec but not yet confirmed as a real problem in practice), or **Resolved** (closed, kept for history).

| Status | Item | Notes |
|---|---|---|
| Resolved | External citation system reference-list overhaul | Reference-list extraction, bibliography-grounded critiques, narrowed missing-baseline search, and unified existence-and-relevance validation are built — see §21-22. |
| Open | Full in-text claim-to-citation accuracy checking | Follow-on work should locate in-text citation markers and verify that each cited work supports the specific surrounding claim across citation styles; the current validator checks existence and topical relevance only. |
| Watching | One-call bibliography extraction under provider token limits | Reference extraction deliberately uses one Groq batch and fails open to `[]`; a very large bibliography or saturated shared TPM may therefore leave `citation_integrity` unavailable. Track extraction-failure metrics before deciding whether provider capacity or the one-call constraint should change. |
| Watching | Citation-relevance threshold calibration | The validator's named `0.45` cosine threshold now catches the required unrelated-work case, but it has not been tuned against a labeled cross-domain citation set. Calibrate before treating small score differences near the boundary as meaningful. |
| Watching | Multi-topic Exhaustive audits and free-tier quota | Flagged as a risk in §16.8 when Phase 3 was speced. Not yet load-tested against real usage now that it's built — worth an actual timed run before recommending Exhaustive depth to anyone. |
| Open (minor) | Stale comment in `constants.py` | `EXCHANGES_PER_ROUND = 3` is still commented `# fixed for Phase 1 (Fast depth)` — harmless, but worth a one-line cleanup next time that file is touched. |
| Not yet reviewed | Frontend coverage of Phase 3 configurability | The Phase 3 backend (auth/RLS, strictness/depth/domain framing, final reports, version diffing) was reviewed in real depth this round. The frontend surfaces for all of it (config screen, mode picker, version upload flow, export button) were not — not confirmed broken, just not checked yet. Worth a pass before leaning on them.

---

## 19. Phase 4 — Technical Specification (Institutional API)

Phase 4 was originally scoped as two pieces — a calibration benchmark and an institutional API. The benchmark has been eliminated (see §18) rather than deferred; this section now covers the API alone. Scope stays deliberately modest — this is a bootstrapped, free-tier project, and the API is sized to prove real value before any heavier investment (billing/tiering) gets built.

**Explicit non-goals:** the citation reference-list overhaul (§18) remains separate from Phase 4 — don't fold it in here. No billing/subscription infrastructure yet — there's no evidence of institutional demand to size it against. No calibration benchmark of any kind — this was a considered and rejected direction, not a backlog item.

### 19.1 Institutional API

Build the technical capability; don't productize it yet.

- **API keys as a second auth path**, not a parallel identity system: a new `api_keys` table (id, user_id, key_hash, created_at, last_used_at, revoked_at) — store only a hash, show the plaintext key once at creation. A key resolves to the same `user_id` a JWT would, so it plugs into the exact same downstream ownership/RLS-scoped logic already built in Phase 3 rather than duplicating it.
- **Self-service key management** under a user's existing account — generate and revoke keys, no separate signup flow.
- **Extend `get_current_user`** to accept either a Supabase JWT or an API key in the Authorization header, rather than building a second dependency with separate logic.
- **Basic usage visibility:** a simple per-key request counter is enough for this phase — not full metering or billing. The goal is knowing whether the API gets real use, which is what would justify building billing later.
- **Version the surface for the first time:** introduce a `/v1/` prefix now, before any external consumer depends on today's paths. This is a one-time reorganization worth doing deliberately rather than letting institutional integrations lock in unversioned paths.
- **Clean up the auto-generated OpenAPI docs** (FastAPI's `/docs`) rather than building a separate documentation site — this is close to free given the API is already typed with Pydantic models.

### 19.2 Non-functional notes

- API keys are a new secret class — make sure they're excluded from any logging (the existing LLM/embedding logging in `llm_client.py` and friends should never have a path where a raw API key could end up in a log line).

---

## 20. Definition of Done — Phase 4

Building on Phases 1-3 (still required), an authenticated user can:
1. Generate an API key from their account, revoke it, and confirm a revoked key stops working immediately.
2. Make an authenticated request to a `/v1/` endpoint using an API key instead of a JWT and get the same data they'd see in the web app, scoped to their own account only.


---

## 21. External Citation System — Overhaul Specification

Numbered after Phase 4 for continuity of the document, but **built before it** — this is the item that was deliberately deferred during Phase 3 planning and is now being picked up first, ahead of the remaining Phase 4 work (the calibration benchmark that was originally planned alongside the institutional API has since been eliminated — see §18).

### 21.1 What's changing, and why this is an upgrade, not just a bug fix

The current mechanism only guards against the *Attacker's own* hallucination: it builds a search query from body-text keywords, sends it to Semantic Scholar/arXiv/OpenAlex, and checks whether whatever comes back exists. On a document with no real academic citation structure (a resume, in the case that surfaced this), it still runs, still gets real-but-irrelevant search hits back, and still validates them as "existing" — because existence was always the only thing being checked.

The overhaul checks something different and more valuable: **whether the paper's own citations are real and topically sound.** That's a genuine research-integrity check (fabricated or misapplied citations are a real and rising concern, more so with LLM-assisted paper writing), not just a defensive measure against the system's own agent hallucinating.

Two citation-critique types exist going forward:
- **`citation_integrity`** *(new, primary)* — the Attacker questions a specific reference the paper *actually cites*, pulled from its own extracted reference list.
- **`missing_baseline`** *(existing, retained but narrowed)* — the Attacker searches external literature for something the paper *doesn't* cite. Kept because it serves a genuinely different purpose (prior-art discovery vs. citation-integrity checking), but now explicitly excludes anything already present in the paper's own extracted reference list, so it can no longer falsely flag something as "missing" that's already cited.

### 21.2 Reference list extraction (ingestion-time)

Runs once per paper at upload, alongside the existing relevance classification and reproducibility scan — not per-audit, not per-exchange.

1. **Deterministic section location:** scan extracted text for a references/bibliography heading (case-insensitive match on standalone lines like "References," "REFERENCES," "Bibliography," "Works Cited"), typically near the end of the document. Take everything from that heading to the end of the document (or the next major heading, e.g. "Appendix," if one follows) as the references block.
2. **Graceful degradation:** if no references section is detected, don't fail the upload — set `reference_list` to empty and disable `citation_integrity` critiques for that paper. `missing_baseline` search (unnarrowed, since there's nothing to exclude against) remains available. A paper without a detected reference list should still be fully auditable, just without this one capability.
3. **LLM-assisted structuring:** one batched call (not one call per reference) parses the references block into `{raw_text, title, authors, year}` entries — citation formats vary too much (numbered, author-year, IEEE, APA, etc.) for a regex parser to handle reliably, but this is exactly the kind of unstructured-to-structured task an LLM handles well. Route through Groq, consistent with the relevance classifier — keeps this off Gemini's more precious quota.
4. **Store** as `papers.reference_list` (JSONB array), each entry with a stable id/index so critiques can reference a specific one.

### 21.3 Attacker integration

For `novelty_scope` and `experimental_setup` (the same two topics as before):

- Give the Attacker the paper's `reference_list` as available context. Prefer `citation_integrity` critiques when a specific reference looks load-bearing to a claim; fall back to `missing_baseline` search when nothing from the reference list stands out.
- `citation_integrity` schema addition: `cited_reference_id` (references an entry in `reference_list`), replacing the free-form external search for this critique type.
- `missing_baseline` unchanged structurally, but the search step now filters any candidate whose title fuzzy-matches an entry already in `reference_list` before it ever reaches the Attacker.

### 21.4 Validation

One unified existence-and-relevance check, applied to both critique types (this also closes the standing gap where `validate_external_citations` only checked existence):

- **Existence:** search Semantic Scholar/arXiv/OpenAlex for the cited title (from `reference_list` for `citation_integrity`, from the search candidate for `missing_baseline`); confirm a real match.
- **Relevance:** compute embedding similarity between the cited work and the paper's own content/topic, using the same infrastructure already built for novelty-overlap ranking. This is a lighter-weight signal than fully matching in-text usage to citation content — it catches a citation that's existent but topically unrelated (a real, if cruder, error signal), not a citation that exists and is on-topic but is being *mischaracterized* in how the paper uses it.
- **Explicitly out of scope for this pass:** full citation-accuracy checking — matching a specific in-text claim ("Smith et al. showed X") against what the cited work actually says. That's a genuinely valuable follow-on capability, but it requires locating in-text citation markers and their surrounding claims across multiple citation styles, which is a meaningfully harder problem than what's being built here. Log it in §18 as a tracked future enhancement rather than folding it in now.

### 21.5 Cost management

Validate lazily — only references the Attacker actually cites during a debate get checked, not the full reference list eagerly at ingestion (a paper can easily have 50-80 references; validating all of them against three rate-limited external APIs on every upload doesn't scale). This matches the existing lazy-validation pattern already used for in-document and external citations elsewhere in the system.

### 21.6 Data model additions

| Entity | Change |
|---|---|
| `papers` | add `reference_list JSONB` — extracted structured references, empty array if none detected |
| Attacker turn schema | add `cited_reference_id` for `citation_integrity` critiques |
| Validator output | add a `reason` field distinguishing "doesn't exist" from "exists but appears topically unrelated," so the Referee (and any retry context back to the Attacker) can be specific about which failure occurred |

### 21.7 Non-functional notes

- This doesn't touch the core 4-node debate graph structure — it changes what grounding data is available to the Attacker and what the validator checks, the same shape of change Phase 2 made originally.
- Test explicitly with a paper that has no detectable references section (not just the resume case) to confirm graceful degradation actually degrades gracefully rather than erroring.

---

## 22. Definition of Done — Citation System Overhaul

1. Upload a real research paper and confirm `reference_list` is populated with recognizable entries from its actual bibliography.
2. Trigger a `citation_integrity` critique and confirm it references a real entry from that paper's own list, not a keyword-guessed search result.
3. Manually corrupt one reference's title before a test run and confirm the existence check catches it.
4. Confirm a topically unrelated-but-real citation is now flagged as suspect by the relevance check, not silently accepted the way "metric-learn" was on the resume.
5. Re-run the original resume upload (or an equivalent non-paper document) and confirm no external citation activity happens on it at all — it should be rejected by the Phase 3 relevance gate before ever reaching this code path.
6. Confirm `missing_baseline` search no longer flags anything already present in the paper's own `reference_list`.
7. Upload a paper with no detectable references section and confirm the audit still completes normally, just without `citation_integrity` critiques.


## 23. Product overhaul — September 2026

This working copy keeps the three-exchange evidence/defense/referee mechanism
and adds a findings-first interface, public worked example, native controls,
searchable reusable paper library, URL history and recovery, renewable PDF
access, password recovery, JSON record export and browser print formatting.
User-facing coverage/scrutiny names changed; existing wire values remain.

Bibliography output is anchored to extracted source text. Retrieval has topic
lenses and excludes prior-claim identifiers from query context. Exact repeated
challenges fail validation. Source context for adjudication is bounded and
explicitly marked when truncated. New turns retain model/prompt provenance and
ruling-check metadata. New reports append a deterministic finding register;
existing stored reports are not rewritten. Similarity and self-reported model
confidence are expressly not calibrated scientific certainty.

Reliability work covers cancellation/admission and comparison locks, completed
partial-topic cards, private exception sanitization, auth-outage distinction,
full-schema readiness with a 30-second cache, and bounded literature caching
(256 entries, 900-second positive/60-second negative lifetime). Frontend
runtime artifact guards reject malformed stream/snapshot content. History
rounds are fetched in batches. No SQL migration was added in this local pass.

Verification: 206 backend tests (205 pass, one opt-in live RLS skip), 23
frontend tests, TypeScript/lint/build, and local synthetic browser journeys.
The synthetic preview tests UI/transport only; its repeated illustrative
findings do not measure reasoning quality.

Remaining acceptance before monetization: a blinded expert-rated comparison
with a strong single-model baseline; calibration and false-accusation metrics;
real two-user RLS and provider-quota integration checks; per-account cost/usage
limits; durable worker ownership and recovery; retention/deletion controls;
and tested billing entitlements. This revisits the earlier decision in §18 to
defer calibration indefinitely: credible paid claims require measured quality.
Full claim-to-cited-source accuracy is still deferred. Multiple roles can share
a model, so their disagreement must not be marketed as independent verification.
