"""Phase 3 product contracts that sit around the unchanged debate cycle.

These tests intentionally exercise behavior rather than implementation details:
configuration has a bounded cost, domain detection stays in the existing relevance
call, domain overrides change both prompts and deterministic evidence, and the
three-exchange inner round remains fixed.
"""

from __future__ import annotations

import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import ValidationError


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents import graph
from app.agents.prompts import attacker_system_prompt
from app.constants import (
    DEFAULT_TOPICS_BY_DEPTH,
    DEPTH_TOPIC_LIMITS,
    DOMAIN_FRAMING,
    EXCHANGES_PER_ROUND,
    ROUND_TOPICS,
    STRICTNESS_FRAMING,
)
from app.models.schemas import AuditCreateRequest, DocumentRelevanceResult
from app.services import relevance_service, reproducibility_service


PAPER_ID = "00000000-0000-4000-8000-000000000101"
ROUND_ID = "00000000-0000-4000-8000-000000000102"


class ProductConfigurationContractTests(unittest.TestCase):
    def test_depth_maps_are_complete_bounded_and_use_known_unique_topics(self) -> None:
        self.assertEqual(
            set(DEPTH_TOPIC_LIMITS),
            {"fast", "deep", "exhaustive"},
        )
        self.assertEqual(set(DEFAULT_TOPICS_BY_DEPTH), set(DEPTH_TOPIC_LIMITS))
        self.assertEqual(DEPTH_TOPIC_LIMITS["fast"], (1, 2))
        self.assertEqual(DEPTH_TOPIC_LIMITS["deep"], (3, 4))
        self.assertEqual(DEPTH_TOPIC_LIMITS["exhaustive"], (5, 6))

        for depth, topics in DEFAULT_TOPICS_BY_DEPTH.items():
            with self.subTest(depth=depth):
                minimum, maximum = DEPTH_TOPIC_LIMITS[depth]
                self.assertGreaterEqual(len(topics), minimum)
                self.assertLessEqual(len(topics), maximum)
                self.assertEqual(len(topics), len(set(topics)))
                self.assertTrue(set(topics).issubset(ROUND_TOPICS))

        self.assertEqual(
            set(DEFAULT_TOPICS_BY_DEPTH["exhaustive"]),
            set(ROUND_TOPICS),
        )
        self.assertLessEqual(len(DEFAULT_TOPICS_BY_DEPTH["fast"]), 2)

    def test_depth_changes_topic_breadth_not_inner_exchange_count(self) -> None:
        self.assertEqual(EXCHANGES_PER_ROUND, 3)
        for depth, topics in DEFAULT_TOPICS_BY_DEPTH.items():
            with self.subTest(depth=depth):
                request = AuditCreateRequest(
                    paper_id=PAPER_ID,
                    depth=depth,
                )
                self.assertEqual(request.round_topics, list(topics))
                self.assertEqual(request.round_topic, topics[0])

    def test_each_depth_accepts_its_documented_topic_count_boundaries(self) -> None:
        available = list(ROUND_TOPICS)
        for depth, (minimum, maximum) in DEPTH_TOPIC_LIMITS.items():
            for count in sorted({minimum, maximum}):
                with self.subTest(depth=depth, count=count):
                    request = AuditCreateRequest(
                        paper_id=PAPER_ID,
                        depth=depth,
                        round_topics=available[:count],
                    )
                    self.assertEqual(request.round_topics, available[:count])

    def test_depth_rejects_wrong_breadth_duplicates_and_unknown_topics(self) -> None:
        invalid_plans = (
            {"depth": "fast", "round_topics": list(ROUND_TOPICS)[:3]},
            {"depth": "deep", "round_topics": list(ROUND_TOPICS)[:2]},
            {"depth": "exhaustive", "round_topics": list(ROUND_TOPICS)[:4]},
            {
                "depth": "fast",
                "round_topics": ["theoretical_soundness", "theoretical_soundness"],
            },
            {"depth": "fast", "round_topics": ["not_a_real_topic"]},
        )
        for plan in invalid_plans:
            with self.subTest(plan=plan), self.assertRaises(ValidationError):
                AuditCreateRequest(paper_id=PAPER_ID, **plan)

    def test_legacy_single_topic_input_remains_a_fast_audit(self) -> None:
        request = AuditCreateRequest(
            paper_id=PAPER_ID,
            round_topic="novelty_scope",
        )
        self.assertEqual(request.depth, "fast")
        self.assertEqual(request.round_topic, "novelty_scope")
        self.assertEqual(request.round_topics, ["novelty_scope"])

    def test_configuration_enums_are_closed(self) -> None:
        valid = AuditCreateRequest(
            paper_id=PAPER_ID,
            strictness_level="brutal",
            depth="fast",
            mode="reviewer_assist",
            domain="life_sciences",
            round_topics=["theoretical_soundness"],
        )
        self.assertEqual(valid.strictness_level, "brutal")
        self.assertEqual(valid.mode, "reviewer_assist")
        self.assertEqual(valid.domain, "life_sciences")

        for field, value in (
            ("strictness_level", "extreme"),
            ("depth", "unbounded"),
            ("mode", "reviewer"),
            ("domain", "biology"),
        ):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                AuditCreateRequest(
                    paper_id=PAPER_ID,
                    round_topics=["theoretical_soundness"],
                    **{field: value},
                )


class RelevanceDomainContractTests(unittest.TestCase):
    def test_typed_result_accepts_only_the_four_domain_slugs(self) -> None:
        for domain in ("ml_cs", "life_sciences", "social_science", "other"):
            with self.subTest(domain=domain):
                result = DocumentRelevanceResult.model_validate(
                    {
                        "is_research_paper": True,
                        "reason": "The excerpt has a scholarly method and results.",
                        "detected_domain": domain,
                    }
                )
                self.assertEqual(result.detected_domain, domain)

        with self.assertRaises(ValidationError):
            DocumentRelevanceResult.model_validate(
                {
                    "is_research_paper": True,
                    "reason": "Looks scholarly.",
                    "detected_domain": "computer_science",
                }
            )

    def test_domain_is_returned_by_the_same_successful_classification_call(self) -> None:
        payload = {
            "is_research_paper": True,
            "reason": "It reports a controlled biomedical experiment.",
            "detected_domain": "life_sciences",
        }
        with (
            patch.object(relevance_service, "GROQ_API_KEY", "configured"),
            patch.object(
                relevance_service.GroqClient,
                "generate",
                return_value=payload,
            ) as groq_generate,
            patch.object(
                relevance_service.GeminiClient,
                "generate",
            ) as gemini_generate,
        ):
            result = relevance_service.classify_document_relevance(
                "Methods: cells were randomly assigned to treatment groups."
            )

        self.assertEqual(result.detected_domain, "life_sciences")
        groq_generate.assert_called_once()
        gemini_generate.assert_not_called()
        combined_prompt = "\n".join(str(arg) for arg in groq_generate.call_args.args)
        self.assertIn("detected_domain", combined_prompt)
        for domain in ("ml_cs", "life_sciences", "social_science", "other"):
            self.assertIn(domain, combined_prompt)

    def test_malformed_domain_is_not_silently_accepted(self) -> None:
        with self.assertRaises(ValueError):
            relevance_service._validate_result(
                {
                    "is_research_paper": True,
                    "reason": "Research-shaped document.",
                    "detected_domain": "medicine",
                }
            )


class DomainReproducibilityContractTests(unittest.TestCase):
    def test_ml_profile_detects_ml_disclosures(self) -> None:
        text = (
            "Our code is available at https://github.com/example/verdict. "
            "The dataset is publicly available. Training used AdamW with a "
            "learning rate, batch size 32, an A100 GPU, and random seed 17."
        )
        result = reproducibility_service.scan_paper_reproducibility(
            [(1, text)],
            "ml_cs",
        )
        self.assertEqual(result["domain"], "ml_cs")
        self.assertTrue(result["code_available"])
        self.assertTrue(result["data_available"])
        self.assertTrue(result["hyperparameters_disclosed"])
        self.assertTrue(result["compute_disclosed"])
        self.assertTrue(result["seed_disclosed"])

    def test_life_sciences_profile_uses_material_deposition_and_ethics_signals(self) -> None:
        text = (
            "Reagents and cell lines are available upon request (RRID:CVCL_1234). "
            "Sequence data were deposited in GenBank under accession ABC123. "
            "A detailed protocol is in the supplementary methods. The institutional "
            "review board approved the study and all participants gave informed consent."
        )
        result = reproducibility_service.scan_paper_reproducibility(
            [(1, text)],
            "life_sciences",
        )
        self.assertEqual(result["domain"], "life_sciences")
        self.assertTrue(result["materials_available"])
        self.assertTrue(result["database_deposition"])
        self.assertTrue(result["protocols_disclosed"])
        self.assertTrue(result["ethics_disclosed"])
        self.assertNotIn("hyperparameters_disclosed", result)

    def test_social_science_profile_uses_preregistration_irb_and_repository(self) -> None:
        text = (
            "The study was pre-registered on https://osf.io/abcd1. IRB approval and "
            "informed consent were obtained. The survey instrument is provided in "
            "the appendix, and raw data are available in the OSF repository."
        )
        result = reproducibility_service.scan_paper_reproducibility(
            [(1, text)],
            "social_science",
        )
        self.assertEqual(result["domain"], "social_science")
        self.assertTrue(result["preregistered"])
        self.assertTrue(result["ethics_approval"])
        self.assertTrue(result["materials_available"])
        self.assertTrue(result["data_available"])
        self.assertTrue(result["instruments_disclosed"])
        self.assertNotIn("compute_disclosed", result)

    def test_unknown_domain_falls_back_to_field_neutral_profile(self) -> None:
        result = reproducibility_service.scan_paper_reproducibility(
            [(1, "Raw data are available in a public repository.")],
            "astronomy",
        )
        self.assertEqual(result["domain"], "other")
        self.assertNotIn("code_available", result)
        self.assertNotIn("preregistered", result)

    def test_all_profiles_can_be_precomputed_without_extra_llm_calls(self) -> None:
        pages = [(1, "Methods and data availability statement.")]
        with patch.object(
            reproducibility_service,
            "scan_paper_reproducibility",
            wraps=reproducibility_service.scan_paper_reproducibility,
        ) as scan:
            profiles = reproducibility_service.scan_reproducibility_by_domain(pages)

        self.assertEqual(
            set(profiles),
            {"ml_cs", "life_sciences", "social_science", "other"},
        )
        self.assertEqual(scan.call_count, 4)


class AttackerFramingContractTests(unittest.TestCase):
    def test_every_strictness_frame_materially_changes_the_attacker_prompt(self) -> None:
        prompts = {
            level: attacker_system_prompt(
                "Theoretical Soundness & Mathematical Rigor",
                "theoretical_soundness",
                level,
                "other",
            )
            for level in STRICTNESS_FRAMING
        }
        self.assertEqual(len(set(prompts.values())), len(STRICTNESS_FRAMING))
        for level, framing in STRICTNESS_FRAMING.items():
            with self.subTest(level=level):
                self.assertIn(framing, prompts[level])

    def test_every_domain_frame_materially_changes_the_attacker_prompt(self) -> None:
        prompts = {
            domain: attacker_system_prompt(
                "Reproducibility, Compute & Ablation Studies",
                "reproducibility",
                "standard",
                domain,
            )
            for domain in DOMAIN_FRAMING
        }
        self.assertEqual(len(set(prompts.values())), len(DOMAIN_FRAMING))
        for domain, framing in DOMAIN_FRAMING.items():
            with self.subTest(domain=domain):
                self.assertIn(framing, prompts[domain])
        self.assertIn("reagent", prompts["life_sciences"].lower())
        self.assertIn("pre-registration", prompts["social_science"].lower())
        self.assertIn("ablations", prompts["ml_cs"].lower())

    @staticmethod
    def _state(**overrides: object) -> dict:
        state = {
            "paper_id": PAPER_ID,
            "round_id": ROUND_ID,
            "round_topic": "theoretical_soundness",
            "round_topic_name": "Theoretical Soundness & Mathematical Rigor",
            "strictness_level": "standard",
            "domain": "other",
            "exchange_number": 1,
            "attacker_retries": 0,
            "prior_claims": [],
            "last_failed_critique": {},
            "last_failure_reason": "",
            "event_callback": None,
        }
        state.update(overrides)
        return state

    def test_attacker_node_threads_selected_strictness_and_domain_to_llm(self) -> None:
        generated = {
            "claim_summary": "A material assumption is unsupported.",
            "critique_text": "The boundary condition is not justified.",
            "cited_chunk_ids": [],
            "external_citations": [],
            "critique_type": "omission",
        }
        with (
            patch.object(graph, "get_llm_client", return_value=Mock()),
            patch.object(graph, "retrieve_chunks", return_value=[]),
            patch.object(
                graph,
                "generate_structured_with_meta",
                return_value=(generated, Mock(), "test-provider"),
            ) as generate,
        ):
            result = graph.attacker_node(
                self._state(strictness_level="brutal", domain="life_sciences")
            )

        system_prompt = generate.call_args.args[1]
        self.assertIn(STRICTNESS_FRAMING["brutal"], system_prompt)
        self.assertIn(DOMAIN_FRAMING["life_sciences"], system_prompt)
        self.assertEqual(result["attacker_output"], generated)

    def test_reproducibility_round_uses_only_the_selected_domain_profile(self) -> None:
        stored_profiles = {
            "by_domain": {
                "ml_cs": {"domain": "ml_cs", "code_available": True},
                "social_science": {
                    "domain": "social_science",
                    "preregistered": True,
                    "ethics_approval": True,
                },
            }
        }
        query = Mock()
        query.select.return_value.eq.return_value.execute.return_value = SimpleNamespace(
            data=[{"reproducibility_signals": stored_profiles}]
        )
        database = Mock()
        database.table.return_value = query
        generated = {
            "claim_summary": "Materials access is underspecified.",
            "critique_text": "The paper does not provide its survey materials.",
            "cited_chunk_ids": [],
            "external_citations": [],
            "critique_type": "omission",
        }
        with (
            patch.object(graph, "get_supabase", return_value=database),
            patch.object(graph, "get_llm_client", return_value=Mock()),
            patch.object(graph, "retrieve_chunks", return_value=[]),
            patch.object(
                graph,
                "generate_structured_with_meta",
                return_value=(generated, Mock(), "test-provider"),
            ) as generate,
        ):
            graph.attacker_node(
                self._state(
                    round_topic="reproducibility",
                    round_topic_name="Reproducibility, Compute & Ablation Studies",
                    domain="social_science",
                )
            )

        user_prompt = generate.call_args.args[2]
        self.assertIn("Domain profile: social_science", user_prompt)
        self.assertIn("Preregistered: True", user_prompt)
        self.assertIn("Ethics Approval: True", user_prompt)
        self.assertNotIn("Code Available", user_prompt)


if __name__ == "__main__":
    unittest.main()
