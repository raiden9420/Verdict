"""
Document Relevance Service — classifies uploaded PDFs to ensure they are academic manuscripts.

Routes calls through GroqClient directly to avoid consuming Gemini free tier quota,
falling back to GeminiClient only if Groq is unconfigured or unavailable.
"""

import logging
from app.config import GROQ_API_KEY
from app.services.llm_client import GroqClient, GeminiClient

logger = logging.getLogger(__name__)


def classify_document_relevance(text_sample: str) -> dict:
    """
    Classify whether a document excerpt represents an academic research paper.

    Parameters
    ----------
    text_sample : str
        First ~1500-2000 words of extracted document text.

    Returns
    -------
    dict with keys:
        is_research_paper: bool
        reason: str
    """
    system_prompt = (
        "You are an academic document classifier. Determine if a given text excerpt "
        "is from a research paper or academic manuscript."
    )
    user_prompt = (
        "You will see an excerpt from an uploaded document. Determine whether this is a "
        "research paper or academic manuscript (has or implies a research question, "
        "methodology, results, or similar academic structure) as opposed to something "
        "else (resume/CV, business document, invoice, news article, marketing material, slide deck, etc.).\n\n"
        f"Document Excerpt:\n{text_sample[:6000]}\n\n"
        "Respond ONLY with valid JSON matching this schema:\n"
        "{\n"
        '  "is_research_paper": true,\n'
        '  "reason": "one sentence explanation"\n'
        "}"
    )

    # 1. Try Groq first to save Gemini quota
    if GROQ_API_KEY:
        try:
            logger.info("Classifying document relevance via GroqClient...")
            groq = GroqClient()
            result = groq.generate(system_prompt, user_prompt)
            if isinstance(result, dict) and "is_research_paper" in result:
                result.setdefault("reason", "Document classification completed.")
                return result
        except Exception as exc:
            logger.warning("Groq relevance classification failed: %s. Falling back to Gemini...", exc)

    # 2. Fall back to Gemini if Groq is not configured or failed
    try:
        logger.info("Classifying document relevance via GeminiClient...")
        gemini = GeminiClient()
        result = gemini.generate(system_prompt, user_prompt)
        if isinstance(result, dict) and "is_research_paper" in result:
            result.setdefault("reason", "Document classification completed.")
            return result
    except Exception as exc:
        logger.warning("Gemini relevance classification failed: %s", exc)

    # 3. If all LLM calls fail, fail open so legitimate uploads are not blocked
    logger.warning("All LLM providers failed for document relevance check — failing open (is_research_paper=True)")
    return {
        "is_research_paper": True,
        "reason": "Relevance check skipped due to LLM provider unavailability.",
    }
