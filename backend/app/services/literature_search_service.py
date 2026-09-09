"""
Literature Search Service — queries Semantic Scholar, arXiv, and OpenAlex in parallel.
Deterministic, NO LLM.

Caches search results in-process. Degrades gracefully if any API fails or times out.
"""

import copy
import re
import ssl
import json
import logging
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from typing import Any

from app.config import OPENALEX_MAILTO
from app.services.embedding_service import embed_batch, cosine_similarity
import certifi

logger = logging.getLogger(__name__)

# Verified SSL context using certifi CA bundle
ssl_ctx = ssl.create_default_context(cafile=certifi.where())


# Bounded process-local cache. Partial sweeps are never cached, and negative
# results expire sooner so newly indexed work can be found without a restart.
_SEARCH_CACHE: OrderedDict[tuple[str, int], tuple[float, list[dict]]] = OrderedDict()
_SEARCH_CACHE_LOCK = threading.Lock()
_SEARCH_CACHE_MAX_ENTRIES = 256
_SEARCH_CACHE_TTL_SECONDS = 900
_SEARCH_NEGATIVE_TTL_SECONDS = 60
LITERATURE_SOURCE_COUNT = 3

# High enough to tolerate punctuation and small extraction/provider differences
# without treating merely related titles as the same cited work.
FUZZY_TITLE_MATCH_THRESHOLD = 0.90


def normalize_title(title: str) -> str:
    """Normalize paper title for deduplication."""
    return re.sub(r"[^a-z0-9]", "", title.lower())


def titles_fuzzy_match(
    left_title: object,
    right_title: object,
    threshold: float = FUZZY_TITLE_MATCH_THRESHOLD,
) -> bool:
    """Return whether two non-empty titles identify the same likely work.

    Normalization handles harmless casing, whitespace, and punctuation drift;
    ``SequenceMatcher`` then tolerates small OCR or provider spelling differences.
    Empty/non-string values never match, preventing malformed metadata from
    filtering a legitimate search result.
    """
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("title-match threshold must be between 0.0 and 1.0")
    if not isinstance(left_title, str) or not isinstance(right_title, str):
        return False

    left = normalize_title(left_title)
    right = normalize_title(right_title)
    if not left or not right:
        return False
    if left == right:
        return True
    return SequenceMatcher(None, left, right).ratio() >= threshold


def filter_candidates_already_referenced(
    candidates: list[dict],
    reference_list: list[dict],
) -> list[dict]:
    """Exclude literature candidates already present in the paper bibliography.

    The input collections are never mutated and candidate order is preserved so
    downstream novelty ranking and prompt formatting remain deterministic.
    """
    reference_titles = [
        entry.get("title")
        for entry in reference_list
        if isinstance(entry, dict)
        and isinstance(entry.get("title"), str)
        and entry["title"].strip()
    ]
    if not reference_titles:
        return list(candidates)

    filtered = [
        candidate
        for candidate in candidates
        if not (
            isinstance(candidate, dict)
            and any(
                titles_fuzzy_match(candidate.get("title"), reference_title)
                for reference_title in reference_titles
            )
        )
    ]
    removed_count = len(candidates) - len(filtered)
    if removed_count:
        logger.info(
            "Filtered %d external candidate(s) already present in the reference list",
            removed_count,
        )
    return filtered


def _reconstruct_openalex_abstract(inverted_index: dict | None) -> str:
    """Reconstruct plain text abstract from OpenAlex abstract_inverted_index."""
    if not inverted_index or not isinstance(inverted_index, dict):
        return ""
    pos_map: dict[int, str] = {}
    for word, positions in inverted_index.items():
        for pos in positions:
            pos_map[pos] = word
    if not pos_map:
        return ""
    max_pos = max(pos_map.keys())
    words = [pos_map.get(i, "") for i in range(max_pos + 1)]
    return " ".join(words).strip()


def search_semantic_scholar(
    query: str,
    limit: int = 5,
    *,
    raise_on_error: bool = False,
) -> list[dict]:
    """Search Semantic Scholar Graph API."""
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"https://api.semanticscholar.org/graph/v1/paper/search?query={encoded_query}&limit={limit}&fields=title,abstract,authors,year,url,externalIds"
        req = urllib.request.Request(url, headers={"User-Agent": "VerdictAuditSystem/1.0"})
        with urllib.request.urlopen(req, timeout=5.0, context=ssl_ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = []
            for item in data.get("data", []):
                title = item.get("title", "")
                if not title:
                    continue
                authors = [a.get("name", "") for a in item.get("authors", []) if a.get("name")]
                results.append({
                    "title": title,
                    "abstract": item.get("abstract") or "",
                    "authors": authors,
                    "year": item.get("year"),
                    "url": item.get("url") or f"https://www.semanticscholar.org/paper/{item.get('paperId', '')}",
                    "source": "Semantic Scholar",
                })
            return results
    except Exception as exc:
        if raise_on_error:
            raise
        logger.warning("Semantic Scholar search failed: %s", type(exc).__name__)
        return []


def search_arxiv(
    query: str,
    limit: int = 5,
    *,
    raise_on_error: bool = False,
) -> list[dict]:
    """Search arXiv API."""
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"https://export.arxiv.org/api/query?search_query=all:{encoded_query}&max_results={limit}"
        req = urllib.request.Request(url, headers={"User-Agent": "VerdictAuditSystem/1.0"})
        with urllib.request.urlopen(req, timeout=5.0, context=ssl_ctx) as resp:
            xml_data = resp.read().decode("utf-8")
            root = ET.fromstring(xml_data)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            results = []
            for entry in root.findall("atom:entry", ns):
                title_elem = entry.find("atom:title", ns)
                summary_elem = entry.find("atom:summary", ns)
                published_elem = entry.find("atom:published", ns)
                id_elem = entry.find("atom:id", ns)

                title = title_elem.text.strip().replace("\n", " ") if title_elem is not None and title_elem.text else ""
                abstract = summary_elem.text.strip().replace("\n", " ") if summary_elem is not None and summary_elem.text else ""
                url = id_elem.text.strip() if id_elem is not None and id_elem.text else ""

                year = None
                if published_elem is not None and published_elem.text:
                    try:
                        year = int(published_elem.text[:4])
                    except ValueError:
                        pass

                authors = [
                    a.find("atom:name", ns).text
                    for a in entry.findall("atom:author", ns)
                    if a.find("atom:name", ns) is not None and a.find("atom:name", ns).text
                ]

                if title:
                    results.append({
                        "title": title,
                        "abstract": abstract,
                        "authors": authors,
                        "year": year,
                        "url": url,
                        "source": "arXiv",
                    })
            return results
    except Exception as exc:
        if raise_on_error:
            raise
        logger.warning("arXiv search failed: %s", type(exc).__name__)
        return []


def search_openalex(
    query: str,
    limit: int = 5,
    *,
    raise_on_error: bool = False,
) -> list[dict]:
    """Search OpenAlex API."""
    try:
        encoded_query = urllib.parse.quote(query)
        mailto = OPENALEX_MAILTO or "verdict@example.com"
        url = f"https://api.openalex.org/works?search={encoded_query}&per-page={limit}&mailto={urllib.parse.quote(mailto)}"
        req = urllib.request.Request(url, headers={"User-Agent": "VerdictAuditSystem/1.0"})
        with urllib.request.urlopen(req, timeout=5.0, context=ssl_ctx) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = []
            for item in data.get("results", []):
                title = item.get("title") or item.get("display_name") or ""
                if not title:
                    continue
                abstract = _reconstruct_openalex_abstract(item.get("abstract_inverted_index"))
                authors = []
                for auth in item.get("authorships", []):
                    author_obj = auth.get("author", {})
                    if author_obj.get("display_name"):
                        authors.append(author_obj["display_name"])

                publication_year = item.get("publication_year")
                work_url = item.get("doi") or item.get("id") or ""

                results.append({
                    "title": title,
                    "abstract": abstract,
                    "authors": authors,
                    "year": publication_year,
                    "url": work_url,
                    "source": "OpenAlex",
                })
            return results
    except Exception as exc:
        if raise_on_error:
            raise
        logger.warning("OpenAlex search failed: %s", type(exc).__name__)
        return []


def search_external_literature(
    query: str,
    limit_per_source: int = 5,
    *,
    with_status: bool = False,
) -> list[dict] | tuple[list[dict], int]:
    """
    Search Semantic Scholar, arXiv, and OpenAlex in parallel.
    Deduplicate by normalized title and cache in-process.
    """
    if not isinstance(limit_per_source, int) or isinstance(limit_per_source, bool) or not 1 <= limit_per_source <= 100:
        raise ValueError("limit_per_source must be an integer between 1 and 100")
    normalized_q = " ".join(query.casefold().split())
    if not normalized_q:
        return ([], 0) if with_status else []
    cache_key = (normalized_q, limit_per_source)
    with _SEARCH_CACHE_LOCK:
        entry = _SEARCH_CACHE.get(cache_key)
        if entry is not None:
            expires_at, cached = entry
            if time.monotonic() < expires_at:
                _SEARCH_CACHE.move_to_end(cache_key)
                result = copy.deepcopy(cached)
                return (result, LITERATURE_SOURCE_COUNT) if with_status else result
            del _SEARCH_CACHE[cache_key]

    logger.info("Searching external literature across APIs (query length %d)", len(query))
    all_results: list[dict] = []
    successful_sources = 0

    with ThreadPoolExecutor(max_workers=LITERATURE_SOURCE_COUNT) as executor:
        futures = {
            executor.submit(
                search_semantic_scholar,
                query,
                limit_per_source,
                raise_on_error=True,
            ): "Semantic Scholar",
            executor.submit(
                search_arxiv,
                query,
                limit_per_source,
                raise_on_error=True,
            ): "arXiv",
            executor.submit(
                search_openalex,
                query,
                limit_per_source,
                raise_on_error=True,
            ): "OpenAlex",
        }
        # Every request has already started in parallel. Read in fixed source
        # order so network timing cannot choose canonical citation metadata.
        for future in futures:
            source_name = futures[future]
            try:
                res = future.result()
                successful_sources += 1
                all_results.extend(res)
            except Exception as exc:
                logger.warning("Error fetching literature from %s: %s", source_name, type(exc).__name__)

    # Deduplicate by normalized title
    seen_titles: set[str] = set()
    deduped: list[dict] = []
    for item in all_results:
        norm = normalize_title(item["title"])
        if norm and norm not in seen_titles:
            seen_titles.add(norm)
            deduped.append(item)

    # A partial provider sweep is useful for the current request, especially
    # when it contains a positive title match, but must not become a permanent
    # cached false negative after a transient outage. Cache only complete
    # sweeps so later calls can retry every source.
    if successful_sources == LITERATURE_SOURCE_COUNT:
        ttl = _SEARCH_CACHE_TTL_SECONDS if deduped else _SEARCH_NEGATIVE_TTL_SECONDS
        with _SEARCH_CACHE_LOCK:
            _SEARCH_CACHE[cache_key] = (time.monotonic() + ttl, copy.deepcopy(deduped))
            _SEARCH_CACHE.move_to_end(cache_key)
            while len(_SEARCH_CACHE) > _SEARCH_CACHE_MAX_ENTRIES:
                _SEARCH_CACHE.popitem(last=False)
    if with_status:
        return deduped, successful_sources
    return deduped


def rank_candidates_by_novelty_overlap(
    paper_abstract: str,
    candidates: list[dict],
    top_k: int = 3,
) -> list[dict]:
    """
    Compute embedding similarity between paper_abstract and candidate abstracts
    for the novelty_scope topic.
    Returns candidates sorted by similarity descending, with similarity_score attached.
    """
    if not paper_abstract or not candidates:
        return candidates[:top_k]

    # One batch call avoids a sequential network round-trip for every candidate.
    candidate_texts = [
        (candidate.get("abstract", "") or candidate.get("title", "") or "Untitled paper")[:1000]
        for candidate in candidates
    ]
    try:
        vectors = embed_batch([paper_abstract[:1000], *candidate_texts])
    except Exception as exc:
        logger.warning("Failed to embed novelty candidates as a batch: %s", type(exc).__name__)
        return candidates[:top_k]

    paper_vec = vectors[0]
    ranked = []
    for cand, cand_vec in zip(candidates, vectors[1:]):
        sim = cosine_similarity(paper_vec, cand_vec)
        cand_copy = dict(cand)
        cand_copy["similarity_score"] = round(sim, 4)
        ranked.append(cand_copy)

    ranked.sort(key=lambda x: x.get("similarity_score", 0.0), reverse=True)
    return ranked[:top_k]
