"""Focused acceptance tests for the external-citation system overhaul.

These tests exercise the citation-specific contracts from System Specs §22
without duplicating the ingestion tests in ``test_reference_service.py``.
All literature searches and embeddings are deterministic test doubles; no test
in this module performs network I/O or calls an LLM.
"""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from app.agents import graph
from app.agents.grounding_validator import validate_citation_critique
from app.agents.schemas import AttackerOutput
from app.services.literature_search_service import (
    filter_candidates_already_referenced,
)


PAPER_ID = "00000000-0000-4000-8000-000000000001"
ROUND_ID = "00000000-0000-4000-8000-000000000002"
CHUNK_ID = "00000000-0000-4000-8000-000000000003"

REFERENCE_LIST = [
    {
        "id": "ref-1",
        "index": 1,
        "raw_text": "A. Author. Background Notes. 2018.",
        "title": "Background Notes",
        "authors": ["A. Author"],
        "year": 2018,
    },
    {
        "id": "ref-2",
        "index": 2,
        "raw_text": (
            "A. Vaswani et al. Attention Is All You Need. NeurIPS, 2017."
        ),
        "title": "Attention Is All You Need",
        "authors": ["A. Vaswani", "et al."],
        "year": 2017,
    },
]

MATCHED_REFERENCE = {
    "title": "Attention Is All You Need",
    "abstract": (
        "A neural sequence transduction architecture based entirely on attention."
    ),
    "authors": ["Ashish Vaswani", "et al."],
    "year": 2017,
    "url": "https://example.test/attention",
    "source": "Semantic Scholar",
}

MISSING_BASELINE = {
    "title": "A Strong Transformer Baseline for Sequence Modelling",
    "abstract": "A controlled transformer baseline for neural sequence modelling.",
    "authors": ["B. Researcher"],
    "year": 2024,
    "url": "https://example.test/transformer-baseline",
    "source": "OpenAlex",
}

PAPER_CONTEXT = (
    "We study attention-only transformer architectures for neural sequence "
    "transduction and machine translation."
)


def _external_citation(candidate: dict) -> dict:
    """Return just the schema-owned metadata copied from a search candidate."""
    return {
        "title": candidate["title"],
        "authors": candidate.get("authors", []),
        "year": candidate.get("year"),
        "url": candidate.get("url", ""),
        "source": candidate["source"],
    }


def _integrity_attack(reference_id: str = "ref-2") -> dict:
    return {
        "claim_summary": "A load-bearing reference may not support this topic.",
        "critique_text": "The cited reference should be checked for integrity.",
        "cited_chunk_ids": [],
        "external_citations": [],
        "critique_type": "citation_integrity",
        "cited_reference_id": reference_id,
    }


def _missing_baseline_attack(candidate: dict = MISSING_BASELINE) -> dict:
    return {
        "claim_summary": "A relevant baseline is missing.",
        "critique_text": "The experiments omit comparison with a relevant baseline.",
        "cited_chunk_ids": [],
        "external_citations": [_external_citation(candidate)],
        "critique_type": "missing_baseline",
        "cited_reference_id": None,
    }


class CitationCritiqueSchemaTests(unittest.TestCase):
    def test_schema_ties_each_citation_type_to_its_authoritative_source(self) -> None:
        integrity = AttackerOutput.model_validate(_integrity_attack())
        self.assertEqual(integrity.cited_reference_id, "ref-2")
        self.assertEqual(integrity.external_citations, [])

        baseline = AttackerOutput.model_validate(_missing_baseline_attack())
        self.assertIsNone(baseline.cited_reference_id)
        self.assertEqual(len(baseline.external_citations), 1)

        invalid_payloads = [
            # Integrity critiques must name one stable, paper-owned reference ID.
            {**_integrity_attack(), "cited_reference_id": None},
            # They cannot replace the canonical reference with model-authored metadata.
            {
                **_integrity_attack(),
                "external_citations": [_external_citation(MATCHED_REFERENCE)],
            },
            # A missing-baseline critique must cite the retrieved external candidate.
            {**_missing_baseline_attack(), "external_citations": []},
            # It must not masquerade as a critique of a reference already in the paper.
            {**_missing_baseline_attack(), "cited_reference_id": "ref-2"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                AttackerOutput.model_validate(payload)

    def test_existing_non_citation_critiques_need_no_reference_id(self) -> None:
        output = AttackerOutput.model_validate({
            "claim_summary": "The sampling assumption is unstated.",
            "critique_text": "No justification is provided for independent sampling.",
            "cited_chunk_ids": [CHUNK_ID],
            "external_citations": [],
            "critique_type": "unstated_assumption",
        })

        self.assertIsNone(output.cited_reference_id)


class MissingBaselineFilteringTests(unittest.TestCase):
    def test_fuzzy_title_filter_excludes_already_cited_candidates(self) -> None:
        candidates = [
            MATCHED_REFERENCE,
            {
                **MATCHED_REFERENCE,
                "title": "Attention Is All You Nee",  # extraction/search typo
                "source": "OpenAlex",
            },
            MISSING_BASELINE,
        ]

        filtered = filter_candidates_already_referenced(candidates, REFERENCE_LIST)

        self.assertEqual(filtered, [MISSING_BASELINE])
        # Filtering is a view over candidates, not permission to mutate search data.
        self.assertEqual(candidates[1]["title"], "Attention Is All You Nee")

    def test_empty_reference_list_leaves_missing_baseline_search_unchanged(self) -> None:
        candidates = [MATCHED_REFERENCE, MISSING_BASELINE]

        filtered = filter_candidates_already_referenced(candidates, [])

        self.assertEqual(filtered, candidates)


class UnifiedCitationValidatorTests(unittest.TestCase):
    def test_integrity_uses_paper_owned_entry_and_rejects_unknown_reference_id(self) -> None:
        with (
            patch(
                "app.agents.grounding_validator.search_external_literature",
                return_value=[MATCHED_REFERENCE],
            ) as search,
            patch(
                "app.agents.grounding_validator.embed_batch",
                return_value=[[1.0, 0.0], [1.0, 0.0]],
            ),
        ):
            results = validate_citation_critique(
                _integrity_attack(),
                REFERENCE_LIST,
                PAPER_CONTEXT,
            )

        self.assertEqual(len(results), 1)
        result = results[0]
        self.assertEqual(search.call_args.args[0], "Attention Is All You Need")
        self.assertEqual(result["reference_id"], "ref-2")
        self.assertEqual(result["title"], "Attention Is All You Need")
        self.assertTrue(result["exists"])
        self.assertTrue(result["relevant"])
        self.assertTrue(result["valid"])
        self.assertEqual(result["reason"], "verified")

        with patch(
            "app.agents.grounding_validator.search_external_literature"
        ) as unknown_search:
            unknown = validate_citation_critique(
                _integrity_attack("ref-does-not-belong-to-paper"),
                REFERENCE_LIST,
                PAPER_CONTEXT,
            )

        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]["reference_id"], "ref-does-not-belong-to-paper")
        self.assertFalse(unknown[0]["exists"])
        self.assertFalse(unknown[0]["relevant"])
        self.assertFalse(unknown[0]["valid"])
        self.assertEqual(unknown[0]["reason"], "reference_not_in_paper")
        unknown_search.assert_not_called()

    def test_corrupted_reference_title_is_reported_as_nonexistent(self) -> None:
        corrupted = [
            {
                **REFERENCE_LIST[1],
                "title": "Attention Is All You Need -- CORRUPTED TITLE",
            }
        ]
        with (
            patch(
                "app.agents.grounding_validator.search_external_literature",
                # A provider may still return a plausible top hit for a corrupt
                # query. Mere presence of that hit must not count as existence.
                return_value=[MATCHED_REFERENCE],
            ) as search,
            patch("app.agents.grounding_validator.embed_batch") as embed,
        ):
            results = validate_citation_critique(
                _integrity_attack(),
                corrupted,
                PAPER_CONTEXT,
            )

        self.assertEqual(search.call_args.args[0], corrupted[0]["title"])
        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["exists"])
        self.assertFalse(results[0]["relevant"])
        self.assertFalse(results[0]["valid"])
        self.assertEqual(results[0]["reason"], "citation_not_found")
        embed.assert_not_called()

    def test_total_search_outage_is_not_misreported_as_a_missing_citation(self) -> None:
        with (
            patch(
                "app.agents.grounding_validator.search_external_literature",
                return_value=([], 0),
            ),
            patch("app.agents.grounding_validator.embed_batch") as embed,
        ):
            results = validate_citation_critique(
                _integrity_attack(),
                REFERENCE_LIST,
                PAPER_CONTEXT,
            )

        self.assertEqual(len(results), 1)
        self.assertFalse(results[0]["validation_complete"])
        self.assertEqual(results[0]["reason"], "existence_check_unavailable")
        embed.assert_not_called()

    def test_partial_search_without_match_is_inconclusive(self) -> None:
        with (
            patch(
                "app.agents.grounding_validator.search_external_literature",
                # arXiv answered, while the broader journal indexes did not.
                return_value=([], 1),
            ),
            patch("app.agents.grounding_validator.embed_batch") as embed,
        ):
            results = validate_citation_critique(
                _integrity_attack(),
                REFERENCE_LIST,
                PAPER_CONTEXT,
            )

        self.assertFalse(results[0]["validation_complete"])
        self.assertEqual(results[0]["reason"], "existence_check_unavailable")
        embed.assert_not_called()

    def test_partial_search_with_positive_match_remains_conclusive(self) -> None:
        with (
            patch(
                "app.agents.grounding_validator.search_external_literature",
                return_value=([MATCHED_REFERENCE], 1),
            ),
            patch(
                "app.agents.grounding_validator.embed_batch",
                return_value=[[1.0, 0.0], [1.0, 0.0]],
            ),
        ):
            results = validate_citation_critique(
                _integrity_attack(),
                REFERENCE_LIST,
                PAPER_CONTEXT,
            )

        self.assertTrue(results[0]["validation_complete"])
        self.assertEqual(results[0]["reason"], "verified")

    def test_real_but_topically_unrelated_reference_is_rejected(self) -> None:
        unrelated_real_work = {
            **MATCHED_REFERENCE,
            "abstract": (
                "A catalogue of medieval pottery glazes from coastal archaeology."
            ),
        }
        with (
            patch(
                "app.agents.grounding_validator.search_external_literature",
                return_value=[unrelated_real_work],
            ),
            patch(
                "app.agents.grounding_validator.embed_batch",
                return_value=[[1.0, 0.0], [0.0, 1.0]],
            ) as embed,
        ):
            results = validate_citation_critique(
                _integrity_attack(),
                REFERENCE_LIST,
                PAPER_CONTEXT,
            )

        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["exists"])
        self.assertFalse(results[0]["relevant"])
        self.assertFalse(results[0]["valid"])
        self.assertEqual(results[0]["reason"], "topically_unrelated")
        embed.assert_called_once()

    def test_same_validator_verifies_integrity_and_missing_baseline_critiques(self) -> None:
        def search_by_title(title: str, *_args, **_kwargs) -> list[dict]:
            if title == MATCHED_REFERENCE["title"]:
                return [MATCHED_REFERENCE]
            if title == MISSING_BASELINE["title"]:
                return [MISSING_BASELINE]
            return []

        with (
            patch(
                "app.agents.grounding_validator.search_external_literature",
                side_effect=search_by_title,
            ) as search,
            patch(
                "app.agents.grounding_validator.embed_batch",
                return_value=[[1.0, 0.0], [1.0, 0.0]],
            ),
        ):
            integrity = validate_citation_critique(
                _integrity_attack(), REFERENCE_LIST, PAPER_CONTEXT
            )
            baseline = validate_citation_critique(
                _missing_baseline_attack(), REFERENCE_LIST, PAPER_CONTEXT
            )

        self.assertEqual(search.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in search.call_args_list],
            [MATCHED_REFERENCE["title"], MISSING_BASELINE["title"]],
        )
        self.assertEqual(integrity[0]["reference_id"], "ref-2")
        self.assertEqual(baseline[0]["citation_index"], 0)
        for result in (integrity[0], baseline[0]):
            with self.subTest(result=result):
                self.assertTrue(result["exists"])
                self.assertTrue(result["relevant"])
                self.assertTrue(result["valid"])
                self.assertEqual(result["reason"], "verified")


class CitationGraphAcceptanceTests(unittest.TestCase):
    @staticmethod
    def _state(**overrides: object) -> dict:
        state = {
            "paper_id": PAPER_ID,
            "round_id": ROUND_ID,
            "round_topic": "novelty_scope",
            "round_topic_name": "Novelty and Scope",
            "strictness_level": "standard",
            "domain": "ml_cs",
            "reference_list": REFERENCE_LIST,
            "paper_context": PAPER_CONTEXT,
            "exchange_number": 1,
            "attacker_retries": 0,
            "prior_claims": [],
            "all_turns": [],
            "all_verdicts": [],
            "attacker_output": {},
            "defender_output": {},
            "attacker_validation": [],
            "defender_validation": [],
            "external_search_results": [],
            "external_validation": [],
            "attacker_valid": True,
            "last_failed_critique": {},
            "last_failure_reason": "",
            "event_callback": None,
            "status": "in_progress",
            "error": "",
            "sequence_counter": 1,
        }
        state.update(overrides)
        return state

    def test_referenced_candidate_is_filtered_before_attacker_prompt(self) -> None:
        already_cited_with_provider_typo = {
            **MATCHED_REFERENCE,
            "title": "Attention Is All You Nee",
        }
        generated_critique = _missing_baseline_attack()

        def preserve_ranked_candidates(
            _paper_text: str,
            candidates: list[dict],
            top_k: int,
        ) -> list[dict]:
            self.assertEqual(top_k, 3)
            self.assertEqual(candidates, [MISSING_BASELINE])
            return candidates

        with (
            patch("app.agents.graph.get_llm_client", return_value=Mock()),
            patch("app.agents.graph.retrieve_chunks", return_value=[{
                "id": CHUNK_ID,
                "text": PAPER_CONTEXT,
                "page_number": 1,
            }]),
            patch(
                "app.agents.graph.search_external_literature",
                return_value=[already_cited_with_provider_typo, MISSING_BASELINE],
            ),
            patch(
                "app.agents.graph.rank_candidates_by_novelty_overlap",
                side_effect=preserve_ranked_candidates,
            ),
            patch(
                "app.agents.graph.generate_structured_with_meta",
                return_value=(generated_critique, Mock(), "test-provider"),
            ) as generate,
        ):
            result = graph.attacker_node(self._state())

        user_prompt = generate.call_args.args[2]
        external_section = user_prompt.split(
            "## Retrieved External Literature Candidates", 1
        )[1].split(
            "## Claims Already Raised This Round", 1
        )[0]
        self.assertNotIn(
            already_cited_with_provider_typo["title"],
            external_section,
        )
        self.assertIn(MISSING_BASELINE["title"], external_section)
        self.assertEqual(result["external_search_results"], [MISSING_BASELINE])

    def test_no_references_disables_integrity_but_other_critiques_still_flow(self) -> None:
        ordinary_critique = {
            "claim_summary": "The sampling assumption is not discussed.",
            "critique_text": "The paper never justifies independent sampling.",
            "cited_chunk_ids": [],
            "external_citations": [],
            "critique_type": "omission",
            "cited_reference_id": None,
        }
        with (
            patch("app.agents.graph.get_llm_client", return_value=Mock()),
            patch("app.agents.graph.retrieve_chunks", return_value=[{
                "id": CHUNK_ID,
                "text": PAPER_CONTEXT,
                "page_number": 1,
            }]),
            patch(
                "app.agents.graph.search_external_literature",
                return_value=[MISSING_BASELINE],
            ) as search,
            patch(
                "app.agents.graph.rank_candidates_by_novelty_overlap",
                return_value=[MISSING_BASELINE],
            ),
            patch(
                "app.agents.graph.generate_structured_with_meta",
                return_value=(ordinary_critique, Mock(), "test-provider"),
            ) as generate,
        ):
            generated = graph.attacker_node(
                self._state(reference_list=[], paper_context="")
            )

        rendered_prompt = "\n".join(str(arg) for arg in generate.call_args.args[1:3])
        self.assertIn("citation_integrity", rendered_prompt)
        self.assertRegex(rendered_prompt.lower(), r"(unavailable|no references detected)")
        self.assertIn("missing_baseline", rendered_prompt)
        self.assertEqual(generated["attacker_output"]["critique_type"], "omission")
        self.assertEqual(generated["paper_context"], PAPER_CONTEXT)
        search.assert_called_once()

        validation_state = self._state(
            reference_list=[],
            paper_context=PAPER_CONTEXT,
            attacker_output=generated["attacker_output"],
            external_search_results=generated["external_search_results"],
        )
        with (
            patch("app.agents.graph.validate_attacker_citations", return_value=[]),
            patch("app.agents.graph.validate_citation_critique") as citation_validator,
            patch(
                "app.agents.graph._store_turn",
                return_value={"agent_type": "attacker"},
            ),
        ):
            accepted = graph.attacker_validator_node(validation_state)

        self.assertTrue(accepted["attacker_valid"])
        citation_validator.assert_not_called()
        self.assertEqual(
            graph.route_after_validation({**validation_state, **accepted}),
            "defender",
        )

    def test_integrity_findings_proceed_but_invalid_missing_baselines_retry(self) -> None:
        integrity_failure = [{
            "citation_index": 0,
            "reference_id": "ref-2",
            "title": MATCHED_REFERENCE["title"],
            "exists": True,
            "relevant": False,
            "valid": False,
            "validation_complete": True,
            "reason": "topically_unrelated",
        }]
        integrity_state = self._state(attacker_output=_integrity_attack())
        with (
            patch("app.agents.graph.validate_attacker_citations", return_value=[]),
            patch(
                "app.agents.graph.validate_citation_critique",
                return_value=integrity_failure,
            ),
            patch(
                "app.agents.graph._store_turn",
                return_value={"agent_type": "attacker"},
            ) as store_integrity,
        ):
            accepted = graph.attacker_validator_node(integrity_state)

        self.assertTrue(accepted["attacker_valid"])
        self.assertEqual(accepted["external_validation"], integrity_failure)
        store_integrity.assert_called_once()
        self.assertEqual(
            graph.route_after_validation({**integrity_state, **accepted}),
            "defender",
        )

        baseline_failure = [{
            "citation_index": 0,
            "reference_id": None,
            "title": MISSING_BASELINE["title"],
            "exists": True,
            "relevant": False,
            "valid": False,
            "validation_complete": True,
            "reason": "topically_unrelated",
        }]
        baseline_state = self._state(
            attacker_output=_missing_baseline_attack(),
            external_search_results=[MISSING_BASELINE],
        )
        with (
            patch("app.agents.graph.validate_attacker_citations", return_value=[]),
            patch(
                "app.agents.graph.validate_citation_critique",
                return_value=baseline_failure,
            ),
            patch("app.agents.graph._store_turn") as store_baseline,
        ):
            rejected = graph.attacker_validator_node(baseline_state)

        self.assertFalse(rejected["attacker_valid"])
        self.assertEqual(rejected["external_validation"], baseline_failure)
        self.assertEqual(rejected["attacker_retries"], 1)
        self.assertIn("topically_unrelated", rejected["last_failure_reason"])
        store_baseline.assert_not_called()
        self.assertEqual(
            graph.route_after_validation({**baseline_state, **rejected}),
            "attacker",
        )


if __name__ == "__main__":
    unittest.main()
