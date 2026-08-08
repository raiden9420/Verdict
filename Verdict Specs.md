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
- Public calibration/benchmark page (verdicts vs. real accept/reject outcomes) `[P4]`
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
- **Phase 4 — Trust at scale:** public calibration benchmark against real review outcomes, institutional API.

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
