"""Regression coverage for source provenance and evidence-aware adjudication."""

from __future__ import annotations

import io
import unittest
import urllib.error
from unittest.mock import Mock, patch

from app.agents import graph, grounding_validator, prompts
from app.agents.schemas import RefereeOutput
from app.services import literature_search_service as literature
from app.services import llm_client, reference_service, report_service
from app.services.retrieval_service import build_attacker_query, build_defender_query
from tests.test_graph_pipeline import CHUNK_ID, PAPER_ID, ROUND_ID, SequenceLLM
from tests.test_report_service import SupabaseStub


def exchange_state(**changes):
    state = {
        "paper_id": PAPER_ID, "round_id": ROUND_ID,
        "round_topic": "theoretical_soundness", "round_topic_name": "Theoretical Soundness",
        "exchange_number": 1, "sequence_counter": 1, "attacker_retries": 0,
        "prior_claims": [], "all_turns": [], "all_verdicts": [],
        "attacker_output": {"claim_summary": "Bounded variance is not justified.",
                            "critique_text": "Which variance condition supports convergence?",
                            "critique_type": "omission", "cited_chunk_ids": []},
        "defender_output": {"rebuttal_text": "The appendix supplies the bound.",
                            "cited_chunk_ids": [CHUNK_ID], "concedes": False},
        "attacker_validation": [], "defender_validation": [], "external_validation": [],
        "reference_list": [], "external_search_results": [], "event_callback": None,
    }
    state.update(changes)
    return state


class BibliographyProvenanceTests(unittest.TestCase):
    def test_legacy_model_metadata_is_replaced_with_source_derived_values(self):
        source = "[1] Smith. A real study. 2024."
        client = Mock()
        client.generate.return_value = {"references": [{
            "raw_text": source, "title": "A real study", "authors": ["Invented Author"], "year": 1901,
        }]}
        result = reference_service.structure_reference_block(source, client=client)
        self.assertEqual(result[0]["authors"], ["Smith"])
        self.assertEqual(result[0]["year"], 2024)

    def test_fabricated_entries_and_titles_never_become_paper_references(self):
        source = "[1] Smith. A real study. 2024."
        client = Mock()
        client.generate.return_value = {"r": [
            {"x": "[9] Ghost. A fabricated study. 2024.", "t": "A fabricated study"},
            {"x": source, "t": "An invented replacement title"},
            {"x": source, "t": "A real study"},
        ]}
        result = reference_service.structure_reference_block(source, client=client)
        self.assertEqual([item["title"] for item in result], ["A real study"])
        self.assertEqual(result[0]["id"], "ref-1")
        client.generate.assert_called_once()

    def test_pdf_ligatures_wraps_and_source_order_are_preserved(self):
        first = "[1] Smith. Efficient inference. 2024."
        second = "[2] Jones. A second study. 2025."
        client = Mock()
        client.generate.return_value = {"r": [
            {"x": second, "t": "A second study"},
            {"x": first, "t": "Efficient inference"},
        ]}
        source = first.replace("Efficient", "Efﬁcient").replace(" inference", "\n inference") + "\n" + second
        result = reference_service.structure_reference_block(source, client=client)
        self.assertEqual([item["title"] for item in result], ["Efficient inference", "A second study"])
        self.assertEqual([item["id"] for item in result], ["ref-1", "ref-2"])


class EvidenceAdjudicationTests(unittest.TestCase):
    def test_optional_citation_chunks_are_validated_and_keep_decisive_source_text(self):
        decisive = "The appendix explicitly proves the required variance bound."
        source = "Opening context. " * 30 + decisive
        for critique_type in ("citation_integrity", "missing_baseline"):
            with self.subTest(critique_type=critique_type), patch.object(
                grounding_validator, "get_chunk_by_id",
                return_value={"id": CHUNK_ID, "paper_id": PAPER_ID, "text": source, "page_number": 7},
            ), patch.object(grounding_validator, "embed_batch", return_value=[[1.0, 0.0], [1.0, 0.0]]):
                result = grounding_validator.validate_attacker_citations({
                    "critique_type": critique_type, "critique_text": decisive, "cited_chunk_ids": [CHUNK_ID],
                }, paper_id=PAPER_ID)
            self.assertEqual(len(result), 1)
            self.assertTrue(result[0]["valid"])
            self.assertIn(decisive, result[0]["chunk_text"])
            self.assertFalse(result[0]["source_truncated"])

    def test_source_passages_have_an_explicit_truncation_boundary(self):
        with patch.object(grounding_validator, "get_chunk_by_id", return_value={
            "text": "a" * 7000, "page_number": 1,
        }), patch.object(grounding_validator, "embed_batch", return_value=[[1.0], [1.0]]):
            result = grounding_validator.validate_citation(CHUNK_ID, "claim", paper_id=PAPER_ID)
        self.assertTrue(result["source_truncated"])
        self.assertEqual(len(result["chunk_text"]), grounding_validator.MAX_SOURCE_PASSAGE_CHARS)

    def test_referee_deduplicates_sources_and_caps_total_passage_context(self):
        validations = [{"chunk_id": f"source-{index}", "chunk_text": "x" * 6000} for index in range(20)]
        sources = graph._referee_sources(exchange_state(
            attacker_validation=validations, defender_validation=validations,
        ))
        self.assertEqual(len(sources), 20)
        self.assertLessEqual(sum(len(item["text"]) for item in sources), 36_000)
        self.assertTrue(all(item["source_truncated"] for item in sources))

    def test_citation_integrity_with_valid_optional_paper_evidence_is_accepted(self):
        state = exchange_state(attacker_output={
            "claim_summary": "A reference deserves a check.", "critique_text": "This passage relies on a reference.",
            "critique_type": "citation_integrity", "cited_chunk_ids": [CHUNK_ID],
            "cited_reference_id": "ref-1", "external_citations": [],
        }, reference_list=[{"id": "ref-1", "title": "A real work"}])
        with patch.object(graph, "validate_attacker_citations", return_value=[{"chunk_id": CHUNK_ID, "valid": True}]), patch.object(graph, "validate_citation_critique", return_value=[{
            "reason": "verified", "valid": True, "validation_complete": True,
        }]), patch.object(graph, "_store_turn", return_value={}) as store:
            result = graph.attacker_validator_node(state)
        self.assertTrue(result["attacker_valid"])
        store.assert_called_once()

    def test_exact_repeats_are_retried_without_validation_or_persistence(self):
        state = exchange_state(prior_claims=["Bounded variance is not justified!"])
        with patch.object(graph, "_store_turn") as store, patch.object(graph, "validate_attacker_citations") as validate:
            result = graph.attacker_validator_node(state)
        self.assertFalse(result["attacker_valid"])
        self.assertEqual(result["attacker_retries"], 1)
        self.assertIn("duplicate_critique", result["last_failure_reason"])
        store.assert_not_called()
        validate.assert_not_called()
        distinct = exchange_state(prior_claims=["The independence assumption is not justified."])
        self.assertFalse(graph._repeated_critique(distinct))

    def test_changed_summary_cannot_hide_an_identical_full_critique(self):
        state = exchange_state(all_turns=[{
            "agent_type": "attacker", "content": {
                "claim_summary": "Different wording", "critique_text": "Which variance condition supports convergence?",
            },
        }])
        self.assertTrue(graph._repeated_critique(state))

    def test_retrieval_uses_distinct_lenses_without_old_claims_or_uuids(self):
        queries = [build_attacker_query("Theory", ["PRIVATE OLD CLAIM"] * count, "theoretical_soundness") for count in range(3)]
        self.assertEqual(len(set(queries)), 3)
        self.assertFalse(any("PRIVATE OLD CLAIM" in query for query in queries))
        self.assertNotIn(CHUNK_ID, build_defender_query("variance bound", [CHUNK_ID]))

    def test_referee_sees_passages_and_saves_model_and_guard_provenance(self):
        decisive = "The full source explicitly states the variance bound on page seven."
        provider = SequenceLLM([{"verdict": "SOLIDIFIED", "confidence": 0.9, "rationale": "The source answers the concern."}])
        state = exchange_state(defender_validation=[{
            "chunk_id": CHUNK_ID, "valid": True, "chunk_text": decisive, "similarity_score": 0.9,
        }])
        with patch.object(graph, "get_llm_client", return_value=provider), patch.object(graph, "_store_turn") as store, patch.object(graph, "_store_verdict"):
            graph.referee_node(state)
        self.assertIn(decisive, provider.prompts[0])
        self.assertIn("NOT entailment", prompts.referee_system_prompt())
        output = store.call_args.args[4]
        self.assertEqual(output["provenance"]["model"], "sequence-test-provider")
        self.assertEqual(output["provenance"]["prompt_version"], prompts.AUDIT_PROMPT_VERSION)
        self.assertIsNone(output["adjudication"]["guard"])

        provider.outputs = [{"verdict": "SOLIDIFIED", "confidence": 0.9, "rationale": "Incorrect model ruling."}]
        state["defender_output"] = {"rebuttal_text": "No answer in these excerpts.", "concedes": True, "cited_chunk_ids": []}
        with patch.object(graph, "get_llm_client", return_value=provider), patch.object(graph, "_store_turn") as store, patch.object(graph, "_store_verdict"):
            graph.referee_node(state)
        output = store.call_args.args[4]
        self.assertEqual(output["verdict"], "ACTIONABLE_FLAW")
        self.assertEqual(output["adjudication"]["initial_verdict"], "SOLIDIFIED")
        self.assertEqual(output["adjudication"]["guard"], "defender_concession")

    def test_disagreement_metadata_distinguishes_a_recheck_from_independent_consensus(self):
        provider = SequenceLLM([
            {"verdict": "SOLIDIFIED", "confidence": 0.3, "rationale": "Possibly responsive."},
            {"verdict": "CONTESTED", "confidence": 0.6, "rationale": "Only partially responsive."},
        ])
        state = exchange_state(defender_validation=[{"chunk_id": CHUNK_ID, "valid": True}])
        with patch.object(graph, "get_llm_client", return_value=provider), patch.object(graph, "_store_turn") as store, patch.object(graph, "_store_verdict"):
            graph.referee_node(state)
        output = store.call_args.args[4]
        self.assertEqual(output["verdict"], "CONTESTED")
        self.assertEqual(output["adjudication"]["consistency_check"], "disagreed")
        self.assertEqual(output["adjudication"]["recheck_verdict"], "CONTESTED")
        self.assertEqual(output["adjudication"]["confidence_kind"], "uncalibrated_model_assessment")


class ProviderSafetyTests(unittest.TestCase):
    def test_provider_error_body_never_reaches_logs_or_public_exception(self):
        secret = "PRIVATE PAPER TEXT AND SECRET TOKEN"
        error = urllib.error.HTTPError("https://provider.invalid", 400, "bad input", {}, io.BytesIO(secret.encode()))
        with patch.object(llm_client.urllib.request, "urlopen", side_effect=error), self.assertLogs(llm_client.logger, level="WARNING") as logs:
            with self.assertRaises(RuntimeError) as raised:
                llm_client.GroqClient(api_key="test").generate("system", "user")
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(secret, " ".join(logs.output))
        self.assertIn("400", str(raised.exception))

    def test_schema_failure_never_logs_model_input(self):
        secret = "PRIVATE PAPER QUOTATION"
        provider = SequenceLLM([{"verdict": "CONTESTED", "confidence": secret, "rationale": "Incomplete."}])
        with self.assertLogs(llm_client.logger, level="WARNING") as logs, self.assertRaises(RuntimeError) as raised:
            llm_client.generate_structured_with_meta(provider, "system", "user", RefereeOutput, max_schema_attempts=1)
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn(secret, " ".join(logs.output))

    def test_gemini_requests_have_a_finite_timeout(self):
        with patch.object(llm_client.genai, "Client") as client:
            llm_client.GeminiClient()
        self.assertEqual(client.call_args.kwargs["http_options"].timeout, 45_000)


class LiteratureCacheTests(unittest.TestCase):
    def setUp(self):
        literature._SEARCH_CACHE.clear()

    def tearDown(self):
        literature._SEARCH_CACHE.clear()

    def test_negative_results_expire_and_partial_results_are_never_cached(self):
        with patch.object(literature, "search_semantic_scholar", return_value=[]) as search, patch.object(literature, "search_arxiv", return_value=[]), patch.object(literature, "search_openalex", return_value=[]), patch.object(literature.time, "monotonic", return_value=0.0) as now:
            literature.search_external_literature("absent")
            literature.search_external_literature("absent")
            self.assertEqual(search.call_count, 1)
            now.return_value = 61.0
            literature.search_external_literature("absent")
            self.assertEqual(search.call_count, 2)

    def test_cache_is_bounded_separates_limits_and_returns_defensive_copies(self):
        paper = {"title": "A Real Work", "authors": ["A. Author"], "source": "Semantic Scholar"}
        with patch.object(literature, "search_semantic_scholar", return_value=[paper]) as search, patch.object(literature, "search_arxiv", return_value=[]), patch.object(literature, "search_openalex", return_value=[]), patch.object(literature, "_SEARCH_CACHE_MAX_ENTRIES", 2):
            first = literature.search_external_literature("topic", 5)
            first[0]["authors"].append("Injected author")
            self.assertEqual(literature.search_external_literature("topic", 5)[0]["authors"], ["A. Author"])
            literature.search_external_literature("topic", 1)
            literature.search_external_literature("another", 5)
            self.assertEqual(search.call_count, 3)
            self.assertEqual(len(literature._SEARCH_CACHE), 2)
            self.assertNotIn(("topic", 5), literature._SEARCH_CACHE)


class FindingRegisterTests(unittest.TestCase):
    def test_register_uses_stored_categories_and_paper_owned_pages(self):
        other_chunk = "00000000-0000-4000-8000-000000000009"
        rows = [{"round_id": ROUND_ID, "exchange_number": exchange, "claim_summary": "A concern",
                 "verdict_type": "CONTESTED", "rationale": "Needs a human check.",
                 "cited_chunk_ids": [CHUNK_ID, other_chunk]} for exchange in range(1, 4)]
        database = SupabaseStub({"verdicts": rows, "chunks": [
            {"id": CHUNK_ID, "paper_id": PAPER_ID, "page_number": 7},
            {"id": other_chunk, "paper_id": "another-paper", "page_number": 99},
        ]})
        findings = report_service._load_report_findings(database, {"paper_id": PAPER_ID}, [
            {"round_id": ROUND_ID, "round_topic_name": "Theory"},
        ])
        self.assertEqual([item["finding_id"] for item in findings], ["T1.E1", "T1.E2", "T1.E3"])
        markdown = report_service.render_finding_register(findings)
        self.assertIn("Open judgment", markdown)
        self.assertIn("PDF p. 7", markdown)
        self.assertNotIn(other_chunk, markdown)
        self.assertNotIn("99", markdown)
        database.tables["verdicts"].pop()
        with self.assertRaises(report_service.FinalReportNotReadyError):
            report_service._load_report_findings(database, {"paper_id": PAPER_ID}, [{"round_id": ROUND_ID, "round_topic_name": "Theory"}])

    def test_evidence_text_cannot_inject_headings_or_remote_markdown_links(self):
        markdown = report_service.render_finding_register([{
            "finding_id": "T1.E1", "topic": "Theory", "verdict_type": "CONTESTED",
            "claim_summary": "Concern\n## Forged section", "rationale": "[Send paper](https://external.invalid)", "sources": [],
        }])
        self.assertNotIn("\n## Forged section", markdown)
        self.assertNotIn("[Send paper](", markdown)


if __name__ == "__main__":
    unittest.main()
