from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services import literature_search_service
from app.services.literature_search_service import (
    FUZZY_TITLE_MATCH_THRESHOLD,
    LITERATURE_SOURCE_COUNT,
    filter_candidates_already_referenced,
    search_external_literature,
    titles_fuzzy_match,
)


class FuzzyTitleMatchingTests(unittest.TestCase):
    def test_default_threshold_handles_formatting_and_small_title_drift(self) -> None:
        self.assertEqual(FUZZY_TITLE_MATCH_THRESHOLD, 0.90)
        self.assertTrue(
            titles_fuzzy_match(
                "Deep Residual Learning for Image Recognition",
                "Deep residual learnng for image recognition.",
            )
        )
        self.assertTrue(
            titles_fuzzy_match(
                "Attention Is All You Need",
                "ATTENTION: IS ALL YOU NEED",
            )
        )
        self.assertFalse(
            titles_fuzzy_match(
                "Deep Residual Learning for Image Recognition",
                "Graph Neural Networks for Molecular Property Prediction",
            )
        )

    def test_empty_malformed_and_invalid_threshold_inputs_are_safe(self) -> None:
        self.assertFalse(titles_fuzzy_match("", "A Real Title"))
        self.assertFalse(titles_fuzzy_match(None, "A Real Title"))
        with self.assertRaises(ValueError):
            titles_fuzzy_match("One", "Two", threshold=1.01)


class ReferencedCandidateFilteringTests(unittest.TestCase):
    def test_filter_removes_exact_and_fuzzy_matches_before_attacker_context(self) -> None:
        exact = {
            "title": "Attention Is All You Need",
            "source": "Semantic Scholar",
        }
        fuzzy = {
            "title": "Deep Residual Learning for Image Recognition",
            "source": "OpenAlex",
        }
        uncited = {
            "title": "Graph Neural Networks for Molecular Property Prediction",
            "source": "arXiv",
        }
        candidates = [exact, fuzzy, uncited]
        references = [
            {"id": "ref-1", "title": "Attention is all you need."},
            {
                "id": "ref-2",
                "title": "Deep residual learnng for image recognition",
            },
        ]

        filtered = filter_candidates_already_referenced(candidates, references)

        self.assertEqual(filtered, [uncited])
        self.assertEqual(candidates, [exact, fuzzy, uncited])
        self.assertEqual(references[0]["title"], "Attention is all you need.")

    def test_empty_or_malformed_reference_titles_leave_candidates_unchanged(self) -> None:
        candidates = [
            {"title": "A Candidate"},
            {"title": "Another Candidate"},
        ]

        filtered = filter_candidates_already_referenced(
            candidates,
            [{"title": ""}, {"raw_text": "missing title"}, "malformed"],
        )

        self.assertEqual(filtered, candidates)
        self.assertIsNot(filtered, candidates)

    def test_malformed_candidate_metadata_is_preserved_not_falsely_filtered(self) -> None:
        malformed = {"abstract": "No provider title was returned."}
        candidates = [malformed, {"title": "An Uncited Work"}]

        filtered = filter_candidates_already_referenced(
            candidates,
            [{"title": "A Cited Work"}],
        )

        self.assertEqual(filtered, candidates)


class SearchAvailabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        literature_search_service._SEARCH_CACHE.clear()
        literature_search_service._SEARCH_STATUS_CACHE.clear()

    def test_incomplete_search_reports_status_and_is_not_cached(self) -> None:
        with (
            patch.object(
                literature_search_service,
                "search_semantic_scholar",
                return_value=[],
            ),
            patch.object(
                literature_search_service,
                "search_arxiv",
                side_effect=RuntimeError("arxiv unavailable"),
            ),
            patch.object(
                literature_search_service,
                "search_openalex",
                side_effect=RuntimeError("openalex unavailable"),
            ),
        ):
            results, successful_sources = search_external_literature(
                "a deliberately absent title",
                with_status=True,
            )

        self.assertEqual(results, [])
        self.assertEqual(successful_sources, 1)
        self.assertNotIn("a deliberately absent title", literature_search_service._SEARCH_CACHE)

        literature_search_service._SEARCH_CACHE.clear()
        literature_search_service._SEARCH_STATUS_CACHE.clear()
        with (
            patch.object(
                literature_search_service,
                "search_semantic_scholar",
                side_effect=RuntimeError("semantic scholar unavailable"),
            ),
            patch.object(
                literature_search_service,
                "search_arxiv",
                side_effect=RuntimeError("arxiv unavailable"),
            ),
            patch.object(
                literature_search_service,
                "search_openalex",
                side_effect=RuntimeError("openalex unavailable"),
            ),
        ):
            results, successful_sources = search_external_literature(
                "provider outage title",
                with_status=True,
            )

        self.assertEqual(results, [])
        self.assertEqual(successful_sources, 0)
        self.assertNotIn("provider outage title", literature_search_service._SEARCH_CACHE)

    def test_complete_empty_search_is_cached_as_conclusive(self) -> None:
        with (
            patch.object(literature_search_service, "search_semantic_scholar", return_value=[]),
            patch.object(literature_search_service, "search_arxiv", return_value=[]),
            patch.object(literature_search_service, "search_openalex", return_value=[]),
        ):
            results, successful_sources = search_external_literature(
                "a title absent from every provider",
                with_status=True,
            )

        self.assertEqual(results, [])
        self.assertEqual(successful_sources, LITERATURE_SOURCE_COUNT)
        self.assertIn("a title absent from every provider", literature_search_service._SEARCH_CACHE)


if __name__ == "__main__":
    unittest.main()
