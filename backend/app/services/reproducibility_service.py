"""Domain-aware deterministic reproducibility signal scanning (no LLM calls)."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

logger = logging.getLogger(__name__)

_DETAIL_LIMIT = 8
_CONTEXT_CHARS = 90


def _compile(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# ML / CS disclosures retained from Phase 2.
CODE_URL_RE = _compile(
    r"https?://(?:www\.)?(?:github\.com|gitlab\.com|huggingface\.co|"
    r"zenodo\.org|bitbucket\.org|anonymous\.4open\.science)/[^\s>)}]+"
)
CODE_TEXT_RE = _compile(
    r"\b(?:code|repository|implementation|source code)\b.{0,60}"
    r"\b(?:available|released|provided|hosted|github|open-source)\b"
)
DATASET_TEXT_RE = _compile(
    r"\b(?:data|dataset|benchmarks?)\b.{0,60}"
    r"\b(?:available|released|provided|downloaded|publicly|zenodo|figshare|"
    r"huggingface\.co/datasets)\b"
)
HYPERPARAM_RE = _compile(
    r"\b(?:learning rate|batch size|weight decay|optimizer|adamw|sgd|epochs|"
    r"warmup|hyperparameters?)\b"
)
COMPUTE_RE = _compile(
    r"\b(?:nvidia|gpu|tpu|v100|a100|h100|rtx|compute cluster|wall-clock|"
    r"gpu hours|training time|hardware|nodes)\b"
)
SEED_RE = _compile(
    r"\b(?:random seed|torch\.manual_seed|np\.random\.seed|set_seed|seeds?)\b"
)

# Life-sciences disclosures.
MATERIAL_RE = _compile(
    r"\b(?:reagents?|materials?|cell lines?|antibod(?:y|ies)|plasmids?|samples?)\b"
    r".{0,90}\b(?:available|availability|provided|deposited|request|addgene|rrid)\b"
)
REAGENT_ID_RE = _compile(r"\b(?:RRID\s*:\s*[A-Z0-9_:-]+|Addgene\s+(?:plasmid\s+)?#?\d+)\b")
DEPOSITION_RE = _compile(
    r"\b(?:GenBank|GEO|SRA|PDB|ENA|ArrayExpress|Dryad|BioProject|dbGaP|"
    r"accession(?: number| code)?|database deposition)\b(?:\s*[:#]?\s*[A-Z]{1,8}[A-Z0-9_.-]{2,})?"
)
PROTOCOL_RE = _compile(
    r"\b(?:detailed protocol|experimental protocol|methods? protocol|"
    r"protocols?\.io|standard operating procedure|supplementary methods?)\b"
)
BIOSCIENCE_ETHICS_RE = _compile(
    r"\b(?:IRB|institutional review board|ethics committee|IACUC|"
    r"informed consent|animal care and use committee)\b"
)

# Social-science disclosures.
PREREG_RE = _compile(
    r"\b(?:pre-?registered|pre-?registration|registered report|aspredicted|"
    r"clinicaltrials\.gov)\b"
)
SOCIAL_ETHICS_RE = _compile(
    r"\b(?:IRB|institutional review board|ethics (?:approval|committee)|"
    r"informed consent|human subjects? approval|exempt status)\b"
)
MATERIALS_REPOSITORY_RE = _compile(
    r"(?:https?://(?:www\.)?(?:osf\.io|dataverse\.[^/\s]+|figshare\.com|"
    r"zenodo\.org|icpsr\.umich\.edu)/[^\s>)}]+|"
    r"\b(?:Open Science Framework|OSF|Dataverse|ICPSR)\b)"
)
INSTRUMENT_RE = _compile(
    r"\b(?:survey instrument|questionnaire|interview guide|coding scheme|"
    r"experimental materials?)\b.{0,80}\b(?:appendix|supplement|available|repository|provided)\b"
)

GENERAL_DATA_RE = _compile(
    r"\b(?:data|dataset|raw data|processed data)\b.{0,80}"
    r"\b(?:available|availability|repository|deposited|shared|accessible)\b"
)


def _details(pattern: re.Pattern[str], text: str) -> list[str]:
    """Return stable, de-duplicated contextual snippets for a disclosure."""
    seen: set[str] = set()
    results: list[str] = []
    for match in pattern.finditer(text):
        start = max(0, match.start() - _CONTEXT_CHARS)
        end = min(len(text), match.end() + _CONTEXT_CHARS)
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()
        key = snippet.casefold()
        if key not in seen:
            seen.add(key)
            results.append(snippet)
        if len(results) >= _DETAIL_LIMIT:
            break
    return results


def _merge_details(*groups: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for value in (item for group in groups for item in group):
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            merged.append(value)
        if len(merged) >= _DETAIL_LIMIT:
            break
    return merged


def _ml_cs_signals(text: str) -> dict:
    code_details = _merge_details(_details(CODE_URL_RE, text), _details(CODE_TEXT_RE, text))
    data_details = _details(DATASET_TEXT_RE, text)
    hp_details = sorted({match.group(0).lower() for match in HYPERPARAM_RE.finditer(text)})
    compute_details = sorted({match.group(0).lower() for match in COMPUTE_RE.finditer(text)})
    seed_details = sorted({match.group(0).lower() for match in SEED_RE.finditer(text)})
    return {
        "domain": "ml_cs",
        "code_available": bool(code_details),
        "code_details": code_details,
        "data_available": bool(data_details),
        "data_details": data_details,
        "hyperparameters_disclosed": len(hp_details) >= 2,
        "hyperparameter_details": hp_details[:10],
        "compute_disclosed": bool(compute_details),
        "compute_details": compute_details[:10],
        "seed_disclosed": bool(seed_details),
        "seed_details": seed_details[:5],
    }


def _life_sciences_signals(text: str) -> dict:
    material_details = _merge_details(
        _details(MATERIAL_RE, text),
        _details(REAGENT_ID_RE, text),
    )
    deposition_details = _details(DEPOSITION_RE, text)
    protocol_details = _details(PROTOCOL_RE, text)
    data_details = _details(GENERAL_DATA_RE, text)
    ethics_details = _details(BIOSCIENCE_ETHICS_RE, text)
    return {
        "domain": "life_sciences",
        "materials_available": bool(material_details),
        "material_details": material_details,
        "database_deposition": bool(deposition_details),
        "deposition_details": deposition_details,
        "protocols_disclosed": bool(protocol_details),
        "protocol_details": protocol_details,
        "data_available": bool(data_details or deposition_details),
        "data_details": _merge_details(data_details, deposition_details),
        "ethics_disclosed": bool(ethics_details),
        "ethics_details": ethics_details,
    }


def _social_science_signals(text: str) -> dict:
    preregistration_details = _details(PREREG_RE, text)
    ethics_details = _details(SOCIAL_ETHICS_RE, text)
    repository_details = _details(MATERIALS_REPOSITORY_RE, text)
    instrument_details = _details(INSTRUMENT_RE, text)
    data_details = _details(GENERAL_DATA_RE, text)
    return {
        "domain": "social_science",
        "preregistered": bool(preregistration_details),
        "preregistration_details": preregistration_details,
        "ethics_approval": bool(ethics_details),
        "ethics_details": ethics_details,
        "materials_available": bool(repository_details or instrument_details),
        "material_details": _merge_details(repository_details, instrument_details),
        "data_available": bool(data_details or repository_details),
        "data_details": _merge_details(data_details, repository_details),
        "instruments_disclosed": bool(instrument_details),
        "instrument_details": instrument_details,
    }


def _other_signals(text: str) -> dict:
    data_details = _details(GENERAL_DATA_RE, text)
    repository_details = _details(MATERIALS_REPOSITORY_RE, text)
    protocol_details = _details(PROTOCOL_RE, text)
    return {
        "domain": "other",
        "data_available": bool(data_details),
        "data_details": data_details,
        "materials_available": bool(repository_details),
        "material_details": repository_details,
        "methods_documented": bool(protocol_details),
        "method_details": protocol_details,
    }


_SCANNERS = {
    "ml_cs": _ml_cs_signals,
    "life_sciences": _life_sciences_signals,
    "social_science": _social_science_signals,
    "other": _other_signals,
}


def scan_paper_reproducibility(
    pages: list[tuple[int, str]],
    domain: str = "ml_cs",
) -> dict:
    """Scan paper text using disclosure expectations appropriate to ``domain``."""
    effective_domain = domain if domain in _SCANNERS else "other"
    full_text = "\n".join(text for _, text in pages if text)
    result = _SCANNERS[effective_domain](full_text)
    present = sum(
        1 for value in result.values() if isinstance(value, bool) and value
    )
    total = sum(1 for value in result.values() if isinstance(value, bool))
    logger.info(
        "Reproducibility scan completed for domain=%s: %d/%d signals present",
        effective_domain,
        present,
        total,
    )
    return result


def scan_reproducibility_by_domain(pages: list[tuple[int, str]]) -> dict[str, dict]:
    """Pre-compute every deterministic profile for cheap audit-time overrides."""
    return {
        domain: scan_paper_reproducibility(pages, domain)
        for domain in _SCANNERS
    }
