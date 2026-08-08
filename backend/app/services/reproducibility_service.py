"""
Reproducibility Signal Scanning Service — deterministic, NO LLM.

Scans extracted paper text for key reproducibility disclosures:
1. Code / repository availability
2. Dataset availability
3. Hyperparameter disclosure
4. Compute / hardware disclosure
5. Random seed disclosure
"""

import re
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex / keyword patterns
# ---------------------------------------------------------------------------
CODE_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:github\.com|gitlab\.com|huggingface\.co|zenodo\.org|bitbucket\.org|anonymous\.4open\.science)/[^\s>)]+",
    re.IGNORECASE,
)
CODE_TEXT_RE = re.compile(
    r"\b(?:code|repository|implementation|source code)\b.{0,60}\b(?:available|released|provided|hosted|github|open-source)\b",
    re.IGNORECASE,
)

DATASET_TEXT_RE = re.compile(
    r"\b(?:data|dataset|benchmarks?)\b.{0,60}\b(?:available|released|provided|downloaded|publicly|zenodo|figshare|huggingface\.co/datasets)\b",
    re.IGNORECASE,
)

HYPERPARAM_RE = re.compile(
    r"\b(?:learning rate|batch size|weight decay|optimizer|adamw|sgd|epochs|warmup|hyperparameters?)\b",
    re.IGNORECASE,
)

COMPUTE_RE = re.compile(
    r"\b(?:nvidia|gpu|tpu|v100|a100|h100|rtx|compute cluster|wall-clock|gpu hours|training time|hardware|nodes)\b",
    re.IGNORECASE,
)

SEED_RE = re.compile(
    r"\b(?:random seed|seed|torch\.manual_seed|np\.random\.seed|set_seed|seeds)\b",
    re.IGNORECASE,
)


def scan_paper_reproducibility(pages: list[tuple[int, str]]) -> dict:
    """
    Scan extracted pages of a paper for reproducibility signals.

    Parameters
    ----------
    pages : list of (page_number, page_text) tuples

    Returns
    -------
    dict matching reproducibility signal checklist schema:
        code_available: bool
        code_details: list of match snippets / URLs
        data_available: bool
        data_details: list of match snippets
        hyperparameters_disclosed: bool
        hyperparameter_details: list of matched keywords/snippets
        compute_disclosed: bool
        compute_details: list of matched keywords/snippets
        seed_disclosed: bool
        seed_details: list of matched keywords/snippets
    """
    full_text = "\n".join(text for _, text in pages)

    # 1. Code links / statements
    code_urls = CODE_URL_RE.findall(full_text)
    code_text_matches = CODE_TEXT_RE.findall(full_text)
    code_available = bool(code_urls or code_text_matches)
    code_details = list(set(code_urls + [m[:100] for m in code_text_matches]))[:5]

    # 2. Dataset availability
    data_matches = DATASET_TEXT_RE.findall(full_text)
    data_available = bool(data_matches)
    data_details = list(set([m[:100] for m in data_matches]))[:5]

    # 3. Hyperparameters
    hp_matches = HYPERPARAM_RE.findall(full_text)
    hp_unique = sorted(list(set(m.lower() for m in hp_matches)))
    hyperparameters_disclosed = len(hp_unique) >= 2  # at least 2 distinct terms
    hp_details = hp_unique[:10]

    # 4. Compute / Hardware
    compute_matches = COMPUTE_RE.findall(full_text)
    compute_unique = sorted(list(set(m.lower() for m in compute_matches)))
    compute_disclosed = len(compute_unique) >= 1
    compute_details = compute_unique[:10]

    # 5. Seeds
    seed_matches = SEED_RE.findall(full_text)
    seed_disclosed = bool(seed_matches)
    seed_details = sorted(list(set(m.lower() for m in seed_matches)))[:5]

    result = {
        "code_available": code_available,
        "code_details": code_details,
        "data_available": data_available,
        "data_details": data_details,
        "hyperparameters_disclosed": hyperparameters_disclosed,
        "hyperparameter_details": hp_details,
        "compute_disclosed": compute_disclosed,
        "compute_details": compute_details,
        "seed_disclosed": seed_disclosed,
        "seed_details": seed_details,
    }

    logger.info(
        "Reproducibility scan completed: code=%s, data=%s, hyperparams=%s, compute=%s, seed=%s",
        code_available,
        data_available,
        hyperparameters_disclosed,
        compute_disclosed,
        seed_disclosed,
    )

    return result
