# Verdict Phase 2 - Depth & Fallback Additions Walkthrough

Phase 2 builds analytical depth, multi-provider resilience, and external literature grounding on top of the working Phase 1 foundation.

## Accomplishments

### 1. Statistical Rigor Round Topic (§14.1)
- Added the 6th fixed round topic `statistical_rigor` ("Statistical Rigor & Methodological Validity") to `constants.py` and `ROUND_TOPICS` in frontend types (`index.ts`).
- Added Attacker framing in `TOPIC_ATTACK_FRAMING` focusing on p-values, sample sizes, power analysis, effect sizes, and multiple hypothesis testing corrections.

### 2. Deterministic Reproducibility Signal Scanning (§14.4)
- **Migration**: Created `backend/migrations/002_add_reproducibility_signals.sql` adding `reproducibility_signals JSONB` to `papers`.
- **Service**: Implemented `app/services/reproducibility_service.py` using deterministic regex/keyword scans for:
  - Code/Repository links (GitHub, GitLab, HuggingFace, Zenodo, Bitbucket)
  - Dataset availability statements
  - Hyperparameter disclosures (learning rate, batch size, optimizer, epochs)
  - Compute & hardware disclosures (NVIDIA GPUs, TPUs, cluster, V100/A100/H100)
  - Random seed disclosures (`torch.manual_seed`, `np.random.seed`, etc.)
- **API & Graph Integration**: Scans papers during `POST /papers` upload, passes findings to the Attacker when auditing `reproducibility`, and attaches the checklist to the Debrief Card.
- **Frontend**: Rendered a structured Reproducibility Checklist card inside the round debrief in `frontend/src/app/page.tsx`.

### 3. External Literature Search & Novelty / Overlap Detection (§14.2 & §14.3)
- **Service**: Created `app/services/literature_search_service.py` to query **Semantic Scholar**, **arXiv**, and **OpenAlex** in parallel via `ThreadPoolExecutor`.
  - Normalizes titles and de-duplicates results across providers.
  - Implements in-process caching.
  - Implements SSL resilience and per-provider fallback (graceful degradation if an API fails/times out).
- **Novelty / Overlap Ranking**: Embeds paper excerpt and external paper abstracts using `embedding_service.py` for `novelty_scope` audits to compute cosine similarity scores (e.g. `0.81` similarity) and rank top candidates.
- **External Citation Validation**: Added `validate_external_citations()` in `grounding_validator.py` to check existence of cited external literature in search results, catching hallucinated paper titles.
- **Agent Integration**: Formats candidate literature into the Attacker prompt for `novelty_scope` and `experimental_setup` topics.

### 4. Multi-Provider LLM Fallback Router (§14.5)
- **Clients**: Added `GroqClient` (`llama-3.3-70b-versatile`) and `OpenRouterClient` (`meta-llama/llama-3.3-70b-instruct`) as subclasses of `LLMClient`.
- **Router**: Created `MultiProviderLLMClient` in `llm_client.py` routing in order: **Gemini → Groq → OpenRouter**.
- **Fallback Logic**: Automatically skips unconfigured providers (missing API keys) and smoothly switches to the next provider if an API hits rate limits (`429`) or errors.
- **Configuration**: Added `GROQ_API_KEY`, `OPENROUTER_API_KEY`, and `OPENALEX_MAILTO` to `.env`, `.env.example`, and `config.py`.

### 5. Self-Consistency Re-runs (§14.6)
- Added `SELF_CONSISTENCY_THRESHOLD = 0.5` in `constants.py`.
- In `referee_node` (`graph.py`), if Referee initial verdict `confidence < 0.5`, an automated re-adjudication pass is executed.
- If the re-run disagrees with the initial verdict, the verdict is forced to `"CONTESTED"` and a note is appended to the rationale explaining the adjudication disagreement.

---

## Verification & Testing Results

1. **Python Compilation**: `python3 -m py_compile` passed across all modified/created files with 0 syntax errors.
2. **Reproducibility Scanner Test**: Verified disclosure detection for code links, hyperparameters, compute hardware, and random seeds.
3. **Literature Search & External Validation Test**:
   - Successfully searched external APIs in parallel.
   - Successfully ranked candidates by embedding similarity.
   - Confirmed `validate_external_citations` returns `valid: True` for real literature and `valid: False` for non-existent paper titles.
4. **Multi-Provider LLM Fallback Test**:
   - Successfully generated responses via `GroqClient` and `OpenRouterClient`.
   - Simulated rate limit (`429`) on `GeminiClient` and verified automatic fallback to `GroqClient` with `status: groq_fallback_success`.

---

## Files Modified & Created

```text
backend/
├── app/
│   ├── constants.py                  (Added statistical_rigor framing, SELF_CONSISTENCY_THRESHOLD)
│   ├── config.py                     (Added GROQ_API_KEY, OPENROUTER_API_KEY, OPENALEX_MAILTO)
│   ├── services/
│   │   ├── reproducibility_service.py (NEW: Deterministic disclosure scanner)
│   │   ├── literature_search_service.py(NEW: Semantic Scholar, arXiv, OpenAlex parallel search & novelty similarity)
│   │   └── llm_client.py             (Added GroqClient, OpenRouterClient, MultiProviderLLMClient fallback router)
│   ├── agents/
│   │   ├── grounding_validator.py    (Added validate_external_citations existence check)
│   │   ├── prompts.py                (Updated Attacker system prompt for external literature & citations)
│   │   └── graph.py                  (Wired literature search, novelty ranking, reproducibility context, & self-consistency)
│   └── api/
│       └── papers.py                 (Ran reproducibility scan during upload and saved to DB)
├── migrations/
│   └── 002_add_reproducibility_signals.sql (NEW: Migration for papers.reproducibility_signals column)
├── .env                              (Configured Groq and OpenRouter keys)
└── .env.example                      (Updated optional env vars)

frontend/
├── src/
│   ├── types/index.ts                (Added statistical_rigor slug, ReproducibilitySignals interface)
│   └── app/page.tsx                  (Rendered Reproducibility Checklist in Debrief Card)
└── README.md                         (Updated features, setup instructions, and architecture details)
```
