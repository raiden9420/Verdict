"""Focused regression tests for the adversarial audit trust boundary."""

from __future__ import annotations

import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch

from app.agents import graph
from app.agents.grounding_validator import (
    validate_attacker_citations,
    validate_citation_critique,
    validate_citation,
)
from app.agents.schemas import AttackerOutput, DefenderOutput, RefereeOutput
from app.constants import (
    EXCHANGES_PER_ROUND,
    GROUNDING_SIMILARITY_THRESHOLD,
    LEXICAL_GROUNDING_SIMILARITY_THRESHOLD,
    MAX_ATTACKER_RETRIES,
)
from app.services.llm_client import (
    GroqClient,
    LLMClient,
    OpenRouterClient,
    generate_structured_with_meta,
)
from app.services.retrieval_service import get_chunk_by_id, retrieve_chunks


PAPER_ID = "00000000-0000-4000-8000-000000000001"
OTHER_PAPER_ID = "00000000-0000-4000-8000-000000000002"
ROUND_ID = "00000000-0000-4000-8000-000000000003"
CHUNK_ID = "00000000-0000-4000-8000-000000000004"


class SequenceLLM(LLMClient):
    def __init__(self, outputs: list[object]) -> None:
        self.outputs = list(outputs)
        self.prompts: list[str] = []
        self.model_name = "sequence-test-provider"

    def generate(self, system_prompt: str, user_prompt: str) -> dict:
        self.prompts.append(user_prompt)
        if not self.outputs:
            raise AssertionError("test LLM ran out of outputs")
        return self.outputs.pop(0)  # type: ignore[return-value]


class QueryStub:
    def __init__(self, data: list[dict]) -> None:
        self.data = data

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, *_args, **_kwargs):
        return self

    def insert(self, *_args, **_kwargs):
        return self

    def update(self, *_args, **_kwargs):
        return self

    def execute(self):
        return type("Result", (), {"data": self.data})()


class SupabaseStub:
    def __init__(self, chunks: list[dict] | None = None) -> None:
        self.chunks = chunks or []

    def table(self, name: str) -> QueryStub:
        if name == "chunks":
            return QueryStub(self.chunks)
        if name == "rounds":
            return QueryStub([{"audit_id": "00000000-0000-4000-8000-000000000005"}])
        return QueryStub([])


class StrictSchemaTests(unittest.TestCase):
    def test_uuid_enum_and_range_contracts_are_strict(self) -> None:
        valid = AttackerOutput.model_validate({
            "claim_summary": "A grounded claim",
            "critique_text": "The paper does not justify this assumption.",
            "cited_chunk_ids": [CHUNK_ID],
            "external_citations": [],
            "critique_type": "unstated_assumption",
        })
        self.assertEqual(valid.cited_chunk_ids, [CHUNK_ID])

        with self.assertRaises(ValueError):
            AttackerOutput.model_validate({
                "claim_summary": "Bad citation",
                "critique_text": "This otherwise looks like valid JSON.",
                "cited_chunk_ids": ["chunk_1"],
                "external_citations": [],
                "critique_type": "inconsistency",
            })
        with self.assertRaises(ValueError):
            RefereeOutput.model_validate({
                "verdict": "SOLID",
                "confidence": 1.1,
                "rationale": "Invalid enum and range.",
            })
        with self.assertRaises(ValueError):
            DefenderOutput.model_validate({
                "rebuttal_text": "Unsupported denial",
                "cited_chunk_ids": [],
                "concedes": False,
            })

    def test_schema_failure_retries_with_correction_context(self) -> None:
        llm = SequenceLLM([
            [],
            {
                "verdict": "CONTESTED",
                "confidence": "high",
                "rationale": "Wrong confidence type",
            },
            {
                "verdict": "CONTESTED",
                "confidence": 0.7,
                "rationale": "Valid on the third attempt.",
            },
        ])
        output, serving, provider = generate_structured_with_meta(
            llm,
            "system",
            "adjudicate",
            RefereeOutput,
        )
        self.assertEqual(output["confidence"], 0.7)
        self.assertIs(serving, llm)
        self.assertEqual(provider, "sequence-test-provider")
        self.assertEqual(len(llm.prompts), 3)
        self.assertIn("Response correction required", llm.prompts[1])

    def test_openai_compatible_providers_retry_transient_http_errors(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                content = json.dumps({"ok": True})
                return json.dumps({
                    "choices": [{"message": {"content": content}}],
                }).encode()

        for client in (GroqClient(api_key="test"), OpenRouterClient(api_key="test")):
            with self.subTest(type(client).__name__):
                transient = urllib.error.HTTPError(
                    client.endpoint,
                    429,
                    "rate limited",
                    {},
                    io.BytesIO(b'{"error":"retry"}'),
                )
                with (
                    patch(
                        "app.services.llm_client.urllib.request.urlopen",
                        side_effect=[transient, Response()],
                    ) as urlopen,
                    patch("app.services.llm_client.time.sleep") as sleep,
                ):
                    self.assertEqual(client.generate("system", "user"), {"ok": True})
                self.assertEqual(urlopen.call_count, 2)
                self.assertEqual(urlopen.call_args.kwargs["timeout"], 30.0)
                sleep.assert_called_once()

    def test_openai_compatible_provider_honors_retry_after_hint(self) -> None:
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                content = json.dumps({"ok": True})
                return json.dumps({
                    "choices": [{"message": {"content": content}}],
                }).encode()

        client = GroqClient(api_key="test")
        transient = urllib.error.HTTPError(
            client.endpoint,
            429,
            "rate limited",
            {"Retry-After": "4"},
            io.BytesIO(b'{"error":{"message":"Please try again in 12.2s."}}'),
        )
        with (
            patch(
                "app.services.llm_client.urllib.request.urlopen",
                side_effect=[transient, Response()],
            ),
            patch("app.services.llm_client.time.sleep") as sleep,
        ):
            self.assertEqual(client.generate("system", "user"), {"ok": True})

        sleep.assert_called_once_with(13)


class RetrievalAndGroundingTests(unittest.TestCase):
    def test_malformed_and_cross_paper_chunk_ids_are_rejected(self) -> None:
        with patch("app.services.retrieval_service.get_supabase") as get_db:
            self.assertIsNone(get_chunk_by_id("chunk_1", paper_id=PAPER_ID))
            get_db.assert_not_called()

        wrong_paper_row = {
            "id": CHUNK_ID,
            "paper_id": OTHER_PAPER_ID,
            "text": "Evidence from another paper",
        }
        with patch(
            "app.services.retrieval_service.get_supabase",
            return_value=SupabaseStub([wrong_paper_row]),
        ):
            self.assertIsNone(get_chunk_by_id(CHUNK_ID, paper_id=PAPER_ID))

    def test_embedding_outage_uses_only_paper_local_lexical_chunks(self) -> None:
        local = {
            "id": CHUNK_ID,
            "paper_id": PAPER_ID,
            "text": "training dataset baseline accuracy ablation",
            "chunk_index": 0,
        }
        foreign = {
            "id": "00000000-0000-4000-8000-000000000006",
            "paper_id": OTHER_PAPER_ID,
            "text": "training dataset baseline accuracy ablation",
            "chunk_index": 1,
        }
        with (
            patch(
                "app.services.retrieval_service._paper_uses_current_embedding_space",
                return_value=True,
            ),
            patch("app.services.retrieval_service.embed_text", side_effect=RuntimeError("quota")),
            patch(
                "app.services.retrieval_service.get_supabase",
                return_value=SupabaseStub([local, foreign]),
            ),
        ):
            chunks = retrieve_chunks(PAPER_ID, "dataset baseline accuracy", top_k=5)
        self.assertEqual([chunk["id"] for chunk in chunks], [CHUNK_ID])
        self.assertEqual(chunks[0]["retrieval_method"], "lexical_fallback")

    def test_empty_vector_result_falls_back_to_existing_paper_chunks(self) -> None:
        local = {
            "id": CHUNK_ID,
            "paper_id": PAPER_ID,
            "text": "training dataset baseline accuracy ablation",
            "chunk_index": 0,
            "retrieval_method": "lexical_fallback",
        }
        database = Mock()
        database.rpc.return_value.execute.return_value = type(
            "Result", (), {"data": []}
        )()
        with (
            patch(
                "app.services.retrieval_service._paper_uses_current_embedding_space",
                return_value=True,
            ),
            patch("app.services.retrieval_service.embed_text", return_value=[0.0] * 384),
            patch("app.services.retrieval_service.get_supabase", return_value=database),
            patch(
                "app.services.retrieval_service._lexical_retrieve",
                return_value=[local],
            ) as lexical,
        ):
            chunks = retrieve_chunks(PAPER_ID, "dataset baseline accuracy", top_k=5)

        self.assertEqual(chunks, [local])
        lexical.assert_called_once_with(PAPER_ID, "dataset baseline accuracy", 5)

    def test_unknown_legacy_embedding_space_never_uses_vector_rpc(self) -> None:
        local = {"id": CHUNK_ID, "paper_id": PAPER_ID, "text": "local evidence"}
        with (
            patch(
                "app.services.retrieval_service._paper_uses_current_embedding_space",
                return_value=False,
            ),
            patch(
                "app.services.retrieval_service._lexical_retrieve",
                return_value=[local],
            ),
            patch("app.services.retrieval_service.embed_text") as embed,
        ):
            chunks = retrieve_chunks(PAPER_ID, "evidence", top_k=3)

        self.assertEqual(chunks, [local])
        embed.assert_not_called()

    def test_grounding_fallback_honors_threshold_boundary(self) -> None:
        self.assertEqual(GROUNDING_SIMILARITY_THRESHOLD, 0.60)
        self.assertEqual(LEXICAL_GROUNDING_SIMILARITY_THRESHOLD, 0.45)
        chunk = {"id": CHUNK_ID, "paper_id": PAPER_ID, "text": "paper evidence"}
        with (
            patch("app.agents.grounding_validator.get_chunk_by_id", return_value=chunk),
            patch("app.agents.grounding_validator.embed_batch", side_effect=RuntimeError("quota")),
            patch(
                "app.agents.grounding_validator.lexical_similarity",
                return_value=LEXICAL_GROUNDING_SIMILARITY_THRESHOLD,
            ),
        ):
            at_boundary = validate_citation(CHUNK_ID, "claim", paper_id=PAPER_ID)
        self.assertTrue(at_boundary["valid"])
        self.assertEqual(at_boundary["validation_method"], "lexical_fallback")

        with (
            patch("app.agents.grounding_validator.get_chunk_by_id", return_value=chunk),
            patch("app.agents.grounding_validator.embed_batch", side_effect=RuntimeError("quota")),
            patch(
                "app.agents.grounding_validator.lexical_similarity",
                return_value=LEXICAL_GROUNDING_SIMILARITY_THRESHOLD - 0.0001,
            ),
        ):
            below = validate_citation(CHUNK_ID, "claim", paper_id=PAPER_ID)
        self.assertFalse(below["valid"])

    def test_multi_citation_claim_uses_one_embedding_batch(self) -> None:
        second_chunk_id = "00000000-0000-4000-8000-000000000007"
        chunks = {
            CHUNK_ID: {"id": CHUNK_ID, "text": "first supporting passage"},
            second_chunk_id: {
                "id": second_chunk_id,
                "text": "second supporting passage",
            },
        }
        with (
            patch(
                "app.agents.grounding_validator.get_chunk_by_id",
                side_effect=lambda chunk_id, paper_id=None: chunks.get(chunk_id),
            ),
            patch(
                "app.agents.grounding_validator.embed_batch",
                return_value=[[1.0, 0.0], [1.0, 0.0], [0.8, 0.6]],
            ) as embed,
        ):
            results = validate_attacker_citations(
                {
                    "critique_type": "inconsistency",
                    "critique_text": "one claim shared by both citations",
                    "cited_chunk_ids": [CHUNK_ID, second_chunk_id],
                },
                paper_id=PAPER_ID,
            )

        self.assertEqual(len(results), 2)
        self.assertTrue(all(result["valid"] for result in results))
        embed.assert_called_once_with([
            "one claim shared by both citations",
            "first supporting passage",
            "second supporting passage",
        ])

    def test_external_validation_requires_a_matching_real_title(self) -> None:
        candidate = {
            "title": "A Robust Baseline for Vision",
            "abstract": "A robust visual recognition baseline.",
            "authors": ["A. Researcher"],
            "year": 2025,
            "url": "https://example.test/paper",
            "source": "OpenAlex",
        }
        attack = {
            "critique_type": "missing_baseline",
            "external_citations": [candidate],
        }
        with (
            patch(
                "app.agents.grounding_validator.search_external_literature",
                return_value=[candidate],
            ),
            patch(
                "app.agents.grounding_validator.embed_batch",
                return_value=[[1.0, 0.0], [1.0, 0.0]],
            ),
        ):
            exact = validate_citation_critique(attack, [], "visual recognition")
        self.assertTrue(exact[0]["valid"])
        self.assertEqual(exact[0]["citation_index"], 0)

        substring_hallucination = {**candidate, "title": "Robust Baseline"}
        with (
            patch(
                "app.agents.grounding_validator.search_external_literature",
                return_value=[candidate],
            ),
            patch("app.agents.grounding_validator.embed_batch") as embed,
        ):
            invalid = validate_citation_critique(
                {
                    "critique_type": "missing_baseline",
                    "external_citations": [substring_hallucination],
                },
                [],
                "visual recognition",
            )
        self.assertFalse(invalid[0]["valid"])
        self.assertEqual(invalid[0]["reason"], "citation_not_found")
        embed.assert_not_called()


class GraphPipelineTests(unittest.TestCase):
    def _initial_state(self, **overrides) -> dict:
        state = {
            "paper_id": PAPER_ID,
            "round_id": ROUND_ID,
            "round_topic": "theoretical_soundness",
            "round_topic_name": "Theoretical Soundness",
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

    def test_non_literature_round_discards_unsourced_external_citations(self) -> None:
        hallucinated = {
            "title": "A Plausible but Unretrieved Paper",
            "authors": ["A. Person"],
            "year": 2024,
            "url": "https://example.test/unretrieved",
            "source": "OpenAlex",
        }
        generated = ({
            "claim_summary": "A subgroup analysis is missing.",
            "critique_text": "The paper does not report subgroup outcomes.",
            "cited_chunk_ids": [],
            "external_citations": [hallucinated],
            "critique_type": "omission",
        }, Mock(), "fake")
        state = self._initial_state(
            round_topic="limitations_impact",
            round_topic_name="Limitations and Impact",
        )
        with (
            patch("app.agents.graph.get_llm_client", return_value=Mock()),
            patch("app.agents.graph.retrieve_chunks", return_value=[]),
            patch("app.agents.graph.generate_structured_with_meta", return_value=generated),
        ):
            result = graph.attacker_node(state)

        self.assertEqual(result["attacker_output"]["external_citations"], [])

    def test_literature_outage_discards_unsourced_external_citations(self) -> None:
        hallucinated = {
            "title": "A Plausible but Unretrieved Paper",
            "authors": ["A. Person"],
            "year": 2024,
            "url": "https://example.test/unretrieved",
            "source": "OpenAlex",
        }
        generated = ({
            "claim_summary": "A prior-art comparison is missing.",
            "critique_text": "The paper does not compare with a standard method.",
            "cited_chunk_ids": [],
            "external_citations": [hallucinated],
            "critique_type": "omission",
        }, Mock(), "fake")
        state = self._initial_state(
            round_topic="novelty_scope",
            round_topic_name="Novelty and Scope",
        )
        with (
            patch("app.agents.graph.get_llm_client", return_value=Mock()),
            patch("app.agents.graph.retrieve_chunks", return_value=[]),
            patch("app.agents.graph.search_external_literature", return_value=[]),
            patch("app.agents.graph.generate_structured_with_meta", return_value=generated),
        ):
            result = graph.attacker_node(state)

        self.assertEqual(result["attacker_output"]["external_citations"], [])
        self.assertEqual(result["attacker_output"]["external_candidate_count"], 0)

    def test_verified_missing_baseline_is_accepted_before_defender(self) -> None:
        external = {
            "title": "A Robust Baseline for Vision",
            "authors": ["A. Researcher"],
            "year": 2025,
            "url": "https://example.test/paper",
            "source": "OpenAlex",
        }
        state = self._initial_state(
            round_topic="novelty_scope",
            attacker_output={
                "claim_summary": "The closest prior method is not discussed.",
                "critique_text": "The contribution omits comparison with a retrieved prior method.",
                "cited_chunk_ids": [],
                "external_citations": [external],
                "critique_type": "missing_baseline",
                "cited_reference_id": None,
            },
            external_search_results=[external],
        )
        with (
            patch("app.agents.graph.validate_attacker_citations", return_value=[]),
            patch(
                "app.agents.graph.validate_citation_critique",
                return_value=[{
                    "citation_index": 0,
                    "title": external["title"],
                    "exists": True,
                    "relevant": True,
                    "valid": True,
                    "similarity_score": 0.91,
                    "validation_complete": True,
                    "reason": "verified",
                }],
            ),
            patch("app.agents.graph._store_turn", return_value={"agent_type": "attacker"}) as store,
        ):
            result = graph.attacker_validator_node(state)
        self.assertTrue(result["attacker_valid"])
        self.assertTrue(result["attacker_output"]["external_citations"][0]["validated"])
        store.assert_called_once()
        self.assertEqual(graph.route_after_validation({**state, **result}), "defender")

    def test_referee_guard_forces_actionable_flaw_on_concession(self) -> None:
        state = self._initial_state(
            attacker_output={
                "claim_summary": "Missing proof",
                "critique_text": "The central bound is not proven.",
                "cited_chunk_ids": [],
                "external_citations": [],
                "critique_type": "omission",
            },
            defender_output={
                "rebuttal_text": "The paper does not contain that proof.",
                "cited_chunk_ids": [],
                "concedes": True,
            },
        )
        solidified = ({
            "verdict": "SOLIDIFIED",
            "confidence": 0.9,
            "rationale": "Model attempted to override the evidence.",
        }, Mock(), "fake")
        with (
            patch("app.agents.graph.get_llm_client", return_value=Mock()),
            patch("app.agents.graph.generate_structured_with_meta", return_value=solidified),
            patch("app.agents.graph._store_turn", return_value={"agent_type": "referee"}),
            patch("app.agents.graph._store_verdict") as store_verdict,
        ):
            store_verdict.return_value = {"exchange_number": 1, "verdict_type": "ACTIONABLE_FLAW"}
            graph.referee_node(state)
        self.assertEqual(store_verdict.call_args.args[3], "ACTIONABLE_FLAW")

    def test_referee_cannot_silently_accept_failed_integrity_checks(self) -> None:
        base_state = self._initial_state(
            attacker_output={
                "claim_summary": "A paper-owned reference is suspect.",
                "critique_text": "The reference needs an integrity check.",
                "cited_chunk_ids": [],
                "cited_reference_id": "ref-1",
                "external_citations": [{"title": "Stored Reference"}],
                "critique_type": "citation_integrity",
            },
            defender_output={
                "rebuttal_text": "The bibliography includes the entry.",
                "cited_chunk_ids": [CHUNK_ID],
                "concedes": False,
            },
            defender_validation=[{"chunk_id": CHUNK_ID, "valid": True}],
        )
        generated_payload = {
            "verdict": "SOLIDIFIED",
            "confidence": 0.9,
            "rationale": "The model attempted to accept the citation.",
        }

        for reason, expected in (
            ("citation_not_found", "ACTIONABLE_FLAW"),
            ("topically_unrelated", "CONTESTED"),
        ):
            state = {
                **base_state,
                "external_validation": [{
                    "citation_type": "citation_integrity",
                    "title": "Stored Reference",
                    "valid": False,
                    "reason": reason,
                }],
            }
            with (
                self.subTest(reason=reason),
                patch("app.agents.graph.get_llm_client", return_value=Mock()),
                patch(
                    "app.agents.graph.generate_structured_with_meta",
                    return_value=(dict(generated_payload), Mock(), "fake"),
                ),
                patch(
                    "app.agents.graph._store_turn",
                    return_value={"agent_type": "referee"},
                ),
                patch("app.agents.graph._store_verdict") as store_verdict,
            ):
                store_verdict.return_value = {
                    "exchange_number": 1,
                    "verdict_type": expected,
                }
                graph.referee_node(state)

            self.assertEqual(store_verdict.call_args.args[3], expected)

    def test_referee_receives_external_validation_and_pins_consistency_provider(self) -> None:
        provider = SequenceLLM([])
        state = self._initial_state(
            attacker_output={
                "claim_summary": "A retrieved baseline is missing.",
                "critique_text": "The paper omits comparison with Prior Work.",
                "cited_chunk_ids": [],
                "external_citations": [{
                    "title": "Prior Work",
                    "authors": ["A. Author"],
                    "year": 2024,
                    "url": "https://example.test/prior",
                    "source": "OpenAlex",
                    "validated": True,
                }],
                "critique_type": "omission",
            },
            defender_output={
                "rebuttal_text": "The comparison appears in the related-work section.",
                "cited_chunk_ids": [CHUNK_ID],
                "concedes": False,
            },
            defender_validation=[{"chunk_id": CHUNK_ID, "valid": True}],
            external_validation=[{
                "title": "Prior Work",
                "valid": True,
                "reason": "matched_retrieved_candidate",
            }],
        )
        outputs = [
            ({"verdict": "CONTESTED", "confidence": 0.2, "rationale": "Unclear."}, provider, "one"),
            ({"verdict": "CONTESTED", "confidence": 0.3, "rationale": "Still unclear."}, provider, "one"),
        ]
        with (
            patch("app.agents.graph.get_llm_client", return_value=Mock()),
            patch("app.agents.graph.generate_structured_with_meta", side_effect=outputs) as generate,
            patch("app.agents.graph._store_turn", return_value={"agent_type": "referee"}),
            patch("app.agents.graph._store_verdict", return_value={
                "exchange_number": 1,
                "verdict_type": "CONTESTED",
            }),
        ):
            graph.referee_node(state)
        self.assertIn("Prior Work", generate.call_args_list[0].args[2])
        self.assertIn("matched_retrieved_candidate", generate.call_args_list[0].args[2])
        self.assertIs(generate.call_args_list[1].kwargs["pinned_client"], provider)

    def test_incomplete_round_cannot_debrief(self) -> None:
        state = self._initial_state(
            all_verdicts=[{"exchange_number": 1}],
            exchange_number=2,
        )
        with self.assertRaisesRegex(RuntimeError, "incomplete audit"):
            graph.debrief_node(state)

    def test_full_graph_has_three_accepted_exchanges_and_no_rejected_turn(self) -> None:
        class PipelineLLM(LLMClient):
            model_name = "pipeline-test-provider"

            def __init__(self) -> None:
                self.attack_count = 0
                self.debrief_prompt = ""

            def generate(self, system_prompt: str, user_prompt: str) -> dict:
                if "You are the Attacker" in system_prompt:
                    self.attack_count += 1
                    return {
                        "claim_summary": (
                            "REJECTED ATTEMPT" if self.attack_count == 1
                            else f"Accepted critique {self.attack_count - 1}"
                        ),
                        "critique_text": "The stated proof assumption lacks sufficient justification.",
                        "cited_chunk_ids": [CHUNK_ID],
                        "external_citations": [],
                        "critique_type": "unstated_assumption",
                    }
                if "You are the Defender" in system_prompt:
                    return {
                        "rebuttal_text": "The cited passage explicitly supplies the assumption.",
                        "cited_chunk_ids": [CHUNK_ID],
                        "concedes": False,
                    }
                if "You are the Referee" in system_prompt:
                    return {
                        "verdict": "SOLIDIFIED",
                        "confidence": 0.9,
                        "rationale": "The defense is fully grounded.",
                    }
                self.debrief_prompt = user_prompt
                return {
                    "executive_synthesis": "Three exchanges were adjudicated.",
                    "solidified_strengths": ["The cited assumptions were supported."],
                    "actionable_weaknesses": [],
                    "contested_points": [],
                }

        llm = PipelineLLM()
        validation_calls = 0

        def validate_attack(_output, paper_id=None):
            nonlocal validation_calls
            validation_calls += 1
            return [{
                "chunk_id": CHUNK_ID,
                "valid": validation_calls != 1,
                "similarity_score": 0.9 if validation_calls != 1 else 0.0,
                "reason": "grounded" if validation_calls != 1 else "insufficient_textual_support",
            }]

        stored_turns: list[dict] = []
        stored_verdicts: list[dict] = []

        def store_turn(round_id, exchange, agent, sequence, content, callback):
            turn = {
                "id": f"turn-{sequence}",
                "round_id": round_id,
                "exchange_number": exchange,
                "agent_type": agent,
                "sequence": sequence,
                "content": content,
            }
            stored_turns.append(turn)
            return turn

        def store_verdict(round_id, exchange, summary, verdict_type, confidence, rationale, cites, callback):
            verdict = {
                "id": f"verdict-{exchange}",
                "round_id": round_id,
                "exchange_number": exchange,
                "claim_summary": summary,
                "verdict_type": verdict_type,
                "confidence": confidence,
                "rationale": rationale,
                "cited_chunk_ids": cites,
            }
            stored_verdicts.append(verdict)
            return verdict

        events: list[dict] = []
        with (
            patch("app.agents.graph.get_llm_client", return_value=llm),
            patch("app.agents.graph.retrieve_chunks", return_value=[{
                "id": CHUNK_ID,
                "paper_id": PAPER_ID,
                "text": "The proof assumes bounded variance and justifies that condition.",
                "page_number": 2,
            }]),
            patch("app.agents.graph.validate_attacker_citations", side_effect=validate_attack),
            patch("app.agents.graph.validate_defender_citations", return_value=[{
                "chunk_id": CHUNK_ID,
                "valid": True,
                "similarity_score": 0.9,
                "reason": "grounded",
            }]),
            patch("app.agents.graph._store_turn", side_effect=store_turn),
            patch("app.agents.graph._store_verdict", side_effect=store_verdict),
            patch("app.agents.graph.get_supabase", return_value=SupabaseStub()),
        ):
            compiled = graph.build_audit_graph().compile()
            with patch("app.agents.graph.get_compiled_graph", return_value=compiled):
                result = graph.run_audit(
                    PAPER_ID,
                    ROUND_ID,
                    "theoretical_soundness",
                    events.append,
                )

        self.assertEqual(len(result["all_verdicts"]), EXCHANGES_PER_ROUND)
        self.assertEqual(len(stored_verdicts), EXCHANGES_PER_ROUND)
        self.assertEqual(result["exchange_number"], EXCHANGES_PER_ROUND + 1)
        self.assertEqual(len(stored_turns), EXCHANGES_PER_ROUND * 4)
        self.assertNotIn("REJECTED ATTEMPT", llm.debrief_prompt)
        self.assertEqual(
            [turn["exchange_number"] for turn in stored_turns if turn["agent_type"] == "attacker"],
            [1, 2, 3],
        )
        self.assertFalse(any(event.get("type") in {"complete", "error"} for event in events))
        self.assertEqual(events[-1]["type"], "debrief")

    def test_literature_round_without_references_completes_normally(self) -> None:
        class NoReferencesLLM(LLMClient):
            model_name = "no-references-test-provider"

            def generate(self, system_prompt: str, user_prompt: str) -> dict:
                if "You are the Attacker" in system_prompt:
                    self.assert_no_integrity_instruction(user_prompt)
                    return {
                        "claim_summary": "The selection procedure is not described.",
                        "critique_text": "The paper omits its sample-selection procedure.",
                        "cited_chunk_ids": [],
                        "cited_reference_id": None,
                        "external_citations": [],
                        "critique_type": "omission",
                    }
                if "You are the Defender" in system_prompt:
                    return {
                        "rebuttal_text": "The manuscript does not provide that detail.",
                        "cited_chunk_ids": [],
                        "concedes": True,
                    }
                if "You are the Referee" in system_prompt:
                    return {
                        "verdict": "ACTIONABLE_FLAW",
                        "confidence": 0.9,
                        "rationale": "The omission was conceded.",
                    }
                return {
                    "executive_synthesis": "The no-reference paper was fully audited.",
                    "solidified_strengths": [],
                    "actionable_weaknesses": ["Document the selection procedure."],
                    "contested_points": [],
                }

            @staticmethod
            def assert_no_integrity_instruction(user_prompt: str) -> None:
                if "citation_integrity is unavailable" not in user_prompt:
                    raise AssertionError("no-reference prompt did not disable integrity")

        llm = NoReferencesLLM()
        stored_verdicts: list[dict] = []

        def store_turn(_round, exchange, agent, sequence, content, _callback):
            return {
                "id": f"turn-{sequence}",
                "exchange_number": exchange,
                "agent_type": agent,
                "sequence": sequence,
                "content": content,
            }

        def store_verdict(_round, exchange, summary, verdict_type, confidence, rationale, cites, _callback):
            verdict = {
                "id": f"verdict-{exchange}",
                "exchange_number": exchange,
                "claim_summary": summary,
                "verdict_type": verdict_type,
                "confidence": confidence,
                "rationale": rationale,
                "cited_chunk_ids": cites,
            }
            stored_verdicts.append(verdict)
            return verdict

        events: list[dict] = []
        with (
            patch("app.agents.graph.get_llm_client", return_value=llm),
            patch("app.agents.graph._load_reference_list", return_value=[]),
            patch("app.agents.graph.search_external_literature", return_value=[]),
            patch("app.agents.graph.retrieve_chunks", return_value=[{
                "id": CHUNK_ID,
                "paper_id": PAPER_ID,
                "text": "This research article reports a controlled experiment.",
                "page_number": 1,
            }]),
            patch("app.agents.graph.validate_attacker_citations", return_value=[]),
            patch("app.agents.graph.validate_citation_critique") as citation_validator,
            patch("app.agents.graph.validate_defender_citations", return_value=[]),
            patch("app.agents.graph._store_turn", side_effect=store_turn),
            patch("app.agents.graph._store_verdict", side_effect=store_verdict),
            patch("app.agents.graph.get_supabase", return_value=SupabaseStub()),
        ):
            compiled = graph.build_audit_graph().compile()
            with patch("app.agents.graph.get_compiled_graph", return_value=compiled):
                result = graph.run_audit(
                    PAPER_ID,
                    ROUND_ID,
                    "novelty_scope",
                    events.append,
                )

        self.assertEqual(len(result["all_verdicts"]), EXCHANGES_PER_ROUND)
        self.assertEqual(len(stored_verdicts), EXCHANGES_PER_ROUND)
        self.assertEqual(events[-1]["type"], "debrief")
        citation_validator.assert_not_called()

    def test_exhausted_attacker_attempts_fail_without_persisting_rejections(self) -> None:
        class InvalidAttackLLM(LLMClient):
            model_name = "invalid-attacker"

            def __init__(self) -> None:
                self.calls = 0

            def generate(self, system_prompt: str, user_prompt: str) -> dict:
                self.calls += 1
                return {
                    "claim_summary": f"Rejected critique {self.calls}",
                    "critique_text": "This critique repeatedly cites unrelated evidence.",
                    "cited_chunk_ids": [CHUNK_ID],
                    "external_citations": [],
                    "critique_type": "inconsistency",
                }

        llm = InvalidAttackLLM()
        events: list[dict] = []
        with (
            patch("app.agents.graph.get_llm_client", return_value=llm),
            patch("app.agents.graph.retrieve_chunks", return_value=[{
                "id": CHUNK_ID,
                "paper_id": PAPER_ID,
                "text": "Unrelated source text.",
                "page_number": 1,
            }]),
            patch("app.agents.graph.validate_attacker_citations", return_value=[{
                "chunk_id": CHUNK_ID,
                "valid": False,
                "similarity_score": 0.0,
                "reason": "insufficient_textual_support",
            }]),
            patch("app.agents.graph._store_turn") as store_turn,
            patch("app.agents.graph._store_verdict") as store_verdict,
            patch("app.agents.graph.get_supabase", return_value=SupabaseStub()),
        ):
            compiled = graph.build_audit_graph().compile()
            with patch("app.agents.graph.get_compiled_graph", return_value=compiled):
                with self.assertRaisesRegex(RuntimeError, "failed to produce a grounded critique"):
                    graph.run_audit(
                        PAPER_ID,
                        ROUND_ID,
                        "theoretical_soundness",
                        events.append,
                    )

        self.assertEqual(llm.calls, MAX_ATTACKER_RETRIES + 1)
        store_turn.assert_not_called()
        store_verdict.assert_not_called()
        self.assertFalse(any(
            event.get("type") in {"turn", "verdict", "debrief", "complete", "error"}
            for event in events
        ))


if __name__ == "__main__":
    unittest.main()
