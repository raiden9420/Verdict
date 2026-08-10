"""
Literature Search Service — queries Semantic Scholar, arXiv, and OpenAlex in parallel.
Deterministic, NO LLM.

Caches search results in-process. Degrades gracefully if any API fails or times out.
"""

import os
import re
import ssl
import json
import logging
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from app.config import OPENALEX_MAILTO
from app.services.embedding_service import embed_text, cosine_similarity
import certifi

logger = logging.getLogger(__name__)

# Verified SSL context using certifi CA bundle
ssl_ctx = ssl.create_default_context(cafile=certifi.where())


# In-process cache: query -> list of paper dicts
_SEARCH_CACHE: dict[str, list[dict]] = {}


def normalize_title(title: str) -> str:
    """Normalize paper title for deduplication."""
    return re.sub(r"[^a-z0-9]", "", title.lower())


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


def search_semantic_scholar(query: str, limit: int = 5) -> list[dict]:
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
        logger.warning("Semantic Scholar search failed: %s", exc)
        return []


def search_arxiv(query: str, limit: int = 5) -> list[dict]:
    """Search arXiv API."""
    try:
        encoded_query = urllib.parse.quote(query)
        url = f"http://export.arxiv.org/api/query?search_query=all:{encoded_query}&max_results={limit}"
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
        logger.warning("arXiv search failed: %s", exc)
        return []


def search_openalex(query: str, limit: int = 5) -> list[dict]:
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
        logger.warning("OpenAlex search failed: %s", exc)
        return []


def search_external_literature(query: str, limit_per_source: int = 5) -> list[dict]:
    """
    Search Semantic Scholar, arXiv, and OpenAlex in parallel.
    Deduplicate by normalized title and cache in-process.
    """
    normalized_q = query.strip().lower()
    if normalized_q in _SEARCH_CACHE:
        logger.info("Returning cached external search results for: '%s'", query)
        return _SEARCH_CACHE[normalized_q]

    logger.info("Searching external literature across APIs for: '%s'", query)
    all_results: list[dict] = []

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(search_semantic_scholar, query, limit_per_source): "Semantic Scholar",
            executor.submit(search_arxiv, query, limit_per_source): "arXiv",
            executor.submit(search_openalex, query, limit_per_source): "OpenAlex",
        }
        for future in as_completed(futures):
            source_name = futures[future]
            try:
                res = future.result()
                all_results.extend(res)
            except Exception as exc:
                logger.warning("Error fetching literature from %s: %s", source_name, exc)

    # Deduplicate by normalized title
    seen_titles: set[str] = set()
    deduped: list[dict] = []
    for item in all_results:
        norm = normalize_title(item["title"])
        if norm and norm not in seen_titles:
            seen_titles.add(norm)
            deduped.append(item)

    _SEARCH_CACHE[normalized_q] = deduped
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

    try:
        paper_vec = embed_text(paper_abstract[:1000])
    except Exception as exc:
        logger.warning("Failed to embed paper abstract for novelty ranking: %s", exc)
        return candidates[:top_k]

    ranked = []
    for cand in candidates:
        cand_abstract = cand.get("abstract", "") or cand.get("title", "")
        if not cand_abstract:
            sim = 0.0
        else:
            try:
                cand_vec = embed_text(cand_abstract[:1000])
                sim = cosine_similarity(paper_vec, cand_vec)
            except Exception as exc:
                logger.warning("Failed to embed candidate '%s': %s", cand.get("title"), exc)
                sim = 0.0

        cand_copy = dict(cand)
        cand_copy["similarity_score"] = round(sim, 4)
        ranked.append(cand_copy)

    ranked.sort(key=lambda x: x.get("similarity_score", 0.0), reverse=True)
    return ranked[:top_k]
