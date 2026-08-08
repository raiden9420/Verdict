"""
Named constants for the Verdict system.
Centralized here so nothing is a magic number and Phase 2+ can tune
any of these from a single location.
"""

# ---------------------------------------------------------------------------
# PDF validation
# ---------------------------------------------------------------------------
MAX_PDF_PAGES = 40
MAX_PDF_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB
MIN_EXTRACTED_TEXT_LENGTH = 100  # chars; below this → likely scanned / image-only

# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
CHUNK_SIZE_WORDS = 400        # target ~300-500 words per chunk
CHUNK_OVERLAP_WORDS = 50      # overlap between consecutive chunks

# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
TOP_K_RETRIEVAL = 5           # chunks returned per similarity search

# ---------------------------------------------------------------------------
# Embeddings (Gemini API — no local model, no PyTorch)
# ---------------------------------------------------------------------------
EMBEDDING_MODEL_NAME = "gemini-embedding-001"
EMBEDDING_DIMENSION = 384     # output_dimensionality sent to Gemini API;
                              # matches existing Supabase vector(384) column

# ---------------------------------------------------------------------------
# Grounding validation
# ---------------------------------------------------------------------------
GROUNDING_SIMILARITY_THRESHOLD = 0.35  # cosine; below → citation invalid

# ---------------------------------------------------------------------------
# Self-consistency check (Phase 2)
# ---------------------------------------------------------------------------
SELF_CONSISTENCY_THRESHOLD = 0.5  # re-run referee if confidence < 0.5

EXCHANGES_PER_ROUND = 3       # fixed for Phase 1 (Fast depth)
MAX_ATTACKER_RETRIES = 2      # retries if attacker's own citations fail

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
GEMINI_MODEL = "gemini-3.1-flash-lite"
LLM_MAX_RETRIES = 5
LLM_BASE_DELAY_SECONDS = 2   # exponential backoff base for 429s

# ---------------------------------------------------------------------------
# Round topics — slug → display name
# The slug is the stored enum value; display name is for prompts & UI.
# ---------------------------------------------------------------------------
ROUND_TOPICS = {
    "novelty_scope": "Novelty, Scope & Problem Formulation",
    "theoretical_soundness": "Theoretical Soundness & Mathematical Rigor",
    "experimental_setup": "Experimental Setup, Datasets & Baselines",
    "reproducibility": "Reproducibility, Compute & Ablation Studies",
    "limitations_impact": "Limitations, Broader Impact & Edge Cases",
    "statistical_rigor": "Statistical Rigor & Methodological Validity",
}

# Topic-specific framing for the Attacker system prompt — tells the Attacker
# what *kind* of weakness to hunt for in each topic area.
TOPIC_ATTACK_FRAMING = {
    "novelty_scope": (
        "Focus on whether the problem is well-posed and the contribution is "
        "genuinely novel. Look for unstated overlap with prior work, vague "
        "claims of novelty without evidence, or a scope that is too narrow / "
        "too broad to support the stated contributions."
    ),
    "theoretical_soundness": (
        "Focus on mathematical derivations, proofs, and theoretical assumptions. "
        "Look for logical gaps, unjustified assumptions, missing edge-case "
        "analysis in proofs, or steps that do not follow from their premises."
    ),
    "experimental_setup": (
        "Focus on experimental design, dataset choices, and baselines. "
        "Look for missing baselines that are standard in this area, unfair "
        "comparisons, dataset biases, or evaluation metrics that do not "
        "align with the paper's claims."
    ),
    "reproducibility": (
        "Focus on whether someone else could reproduce these results. "
        "Look for missing hyperparameters, undisclosed compute requirements, "
        "absent ablation studies, or reliance on proprietary data or code "
        "without an availability statement."
    ),
    "limitations_impact": (
        "Focus on whether the paper honestly addresses where its approach "
        "breaks down. Look for missing discussion of failure modes, societal "
        "impact, distributional assumptions, or edge cases that the method "
        "would not handle."
    ),
    "statistical_rigor": (
        "Focus on statistical methodology, sample sizes, p-values, power analysis, "
        "effect sizes, multiple hypothesis testing corrections, and whether the "
        "reported statistical significance genuinely supports the claims made."
    ),
}

