from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api import papers
from app.services import llm_client, reference_service
from app.services.llm_client import GroqClient
from app.services.reference_service import (
    extract_reference_list,
    locate_reference_block,
    structure_reference_block,
)


class ReferenceSectionLocationTests(unittest.TestCase):
    def test_numbered_references_are_located_and_stop_before_appendix(self) -> None:
        pages = [
            (
                1,
                "\n".join(
                    [
                        "A Study of Residual Learning",
                        "Abstract",
                        "We evaluate deep image classifiers.",
                        "1 Introduction",
                        "Prior work motivates residual mappings.",
                        "2 Methods",
                        "The model uses shortcut connections.",
                        "3 Results",
                        "Residual models improve accuracy.",
                        "7 REFERENCES",
                        "[1] K. He et al. Deep Residual Learning for Image Recognition. 2016.",
                        "[2] A. Krizhevsky et al. ImageNet Classification with Deep CNNs. 2012.",
                        "A. Additional Results",
                        "This appendix is not bibliography text.",
                    ]
                ),
            )
        ]

        block = locate_reference_block(pages)

        self.assertIsNotNone(block)
        self.assertIn("Deep Residual Learning", block or "")
        self.assertIn("ImageNet Classification", block or "")
        self.assertNotIn("Additional Results", block or "")

    def test_author_year_works_cited_ignores_contents_match(self) -> None:
        pages = [
            (
                1,
                "\n".join(
                    [
                        "Contents",
                        "Works Cited",
                        "Introduction",
                        "Background",
                        "Theory",
                        "Data",
                        "Methods",
                        "Sampling",
                        "Measures",
                        "Analysis",
                        "Results",
                        "Robustness",
                        "Discussion",
                        "Limitations",
                        "Conclusion",
                        "Acknowledgements",
                        "Works Cited",
                        "Pearl, J. (2009). Causality: Models, Reasoning, and Inference.",
                        "Rubin, D. B. (1974). Estimating causal effects of treatments.",
                    ]
                ),
            )
        ]

        block = locate_reference_block(pages)

        self.assertIsNotNone(block)
        self.assertTrue((block or "").startswith("Pearl, J."))
        self.assertNotIn("Introduction", block or "")

    def test_no_clean_references_heading_returns_none(self) -> None:
        pages = [
            (
                1,
                "This manuscript cites prior work inline but has no clean bibliography "
                "section heading. References to methods remain in prose.",
            )
        ]

        self.assertIsNone(locate_reference_block(pages))

    def test_unlabelled_major_section_on_new_page_ends_reference_block(self) -> None:
        pages = [
            (
                1,
                "Abstract\nMethods\nResults\nConclusion\nReferences\n"
                "[1] A. Author. A cited method. 2020.",
            ),
            (2, "[2] B. Author. Another cited method. 2021."),
            (
                3,
                "Attention Visualizations\nThis figure discussion is appendix content.",
            ),
        ]

        block = locate_reference_block(pages)

        self.assertIn("A cited method", block or "")
        self.assertIn("Another cited method", block or "")
        self.assertNotIn("Attention Visualizations", block or "")
        self.assertNotIn("appendix content", block or "")

    def test_appendix_title_after_author_year_entries_ends_block(self) -> None:
        pages = [
            (
                1,
                "Introduction\nMethods\nResults\nDiscussion\nConclusion\n"
                "Bibliography\nSmith, A. 2020. A relevant study. Journal 2:1-8.\n"
                "Appendix for “A Relevant Study”\nAdditional model details.",
            )
        ]

        block = locate_reference_block(pages)

        self.assertIn("Smith, A.", block or "")
        self.assertNotIn("Additional model details", block or "")


class ReferenceStructuringTests(unittest.TestCase):
    def test_general_groq_client_defaults_remain_unchanged(self) -> None:
        with patch.object(
            llm_client,
            "_generate_openai_compatible",
            return_value={"ok": True},
        ) as generate:
            result = GroqClient(api_key="test-key").generate("system", "user")

        self.assertEqual(result, {"ok": True})
        payload = generate.call_args.kwargs["payload"]
        self.assertEqual(payload["model"], "llama-3.3-70b-versatile")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertNotIn("max_completion_tokens", payload)
        self.assertNotIn("reasoning_effort", payload)
        self.assertNotIn("reasoning_format", payload)

    def test_default_structurer_uses_current_groq_production_model(self) -> None:
        client = Mock()
        client.generate.return_value = {"references": []}
        with (
            patch.object(reference_service, "GROQ_API_KEY", "configured"),
            patch.object(
                reference_service,
                "GroqClient",
                return_value=client,
            ) as client_class,
        ):
            references = structure_reference_block("[1] A. Author. A work. 2024.")

        self.assertEqual(references, [])
        client_class.assert_called_once_with(
            model_name="openai/gpt-oss-120b",
            response_format=reference_service._REFERENCE_RESPONSE_FORMAT,
            max_completion_tokens=4500,
            reasoning_effort="low",
            reasoning_format="hidden",
        )

    def test_one_batched_call_structures_numbered_entries_with_stable_ids(self) -> None:
        client = Mock()
        client.generate.return_value = {
            "references": [
                {
                    "raw_text": "[1] Vaswani et al. Attention Is All You Need. 2017.",
                    "title": "Attention Is All You Need",
                    "authors": ["Ashish Vaswani", "et al."],
                    "year": "2017",
                },
                {
                    "raw_text": "[2] Bahdanau et al. Neural Machine Translation. 2015.",
                    "title": "Neural Machine Translation by Jointly Learning to Align and Translate",
                    "authors": "Dzmitry Bahdanau et al.",
                    "year": 2015,
                },
            ]
        }

        references = structure_reference_block(
            "[1] Vaswani et al...\n[2] Bahdanau et al...",
            client=client,
        )

        client.generate.assert_called_once()
        self.assertEqual([entry["id"] for entry in references], ["ref-1", "ref-2"])
        self.assertEqual([entry["index"] for entry in references], [1, 2])
        self.assertEqual(references[0]["year"], 2017)
        self.assertEqual(references[1]["authors"], ["Dzmitry Bahdanau et al."])
        system_prompt, user_prompt = client.generate.call_args.args
        self.assertIn("untrusted", system_prompt.lower())
        self.assertIn("UNTRUSTED_REFERENCES_BLOCK_JSON", user_prompt)

    def test_compact_wire_response_derives_metadata_for_common_formats(self) -> None:
        client = Mock()
        client.generate.return_value = {
            "r": [
                {
                    "x": "Smith, A., and Jones, B. 2020. A relevant study. Journal 2:1-8.",
                    "t": "A relevant study",
                },
                {
                    "x": '[12] A. Vaswani et al., "Attention Is All You Need," NeurIPS, 2017.',
                    "t": "Attention Is All You Need",
                },
                {
                    "x": "[13] A. Author. A preprint title. arXiv:1808.04444, 2018.",
                    "t": "A preprint title",
                },
            ]
        }

        references = structure_reference_block("References block", client=client)

        self.assertEqual(len(references), 3)
        self.assertEqual(references[0]["authors"], ["Smith, A., and Jones, B"])
        self.assertEqual(references[0]["year"], 2020)
        self.assertEqual(references[1]["authors"], ["A. Vaswani et al"])
        self.assertEqual(references[1]["year"], 2017)
        self.assertEqual(references[2]["year"], 2018)
        self.assertEqual(
            references[1]["raw_text"],
            '[12] A. Vaswani et al., "Attention Is All You Need," NeurIPS, 2017.',
        )

    def test_oversized_block_is_bounded_before_the_provider_call(self) -> None:
        client = Mock()
        client.generate.return_value = {"references": []}
        oversized = "A" * (reference_service._MAX_REFERENCE_BLOCK_CHARS + 1000)

        references = structure_reference_block(oversized, client=client)

        self.assertEqual(references, [])
        user_prompt = client.generate.call_args.args[1]
        self.assertLess(len(user_prompt), len(oversized))
        self.assertNotIn("A" * (reference_service._MAX_REFERENCE_BLOCK_CHARS + 1), user_prompt)

    def test_pdf_visual_line_wraps_are_compacted_before_provider_call(self) -> None:
        client = Mock()
        client.generate.return_value = {"references": []}

        structure_reference_block(
            "Smith, A. 2020. A\nvisually wrapped\nreference entry.",
            client=client,
        )

        user_prompt = client.generate.call_args.args[1]
        self.assertIn("Smith, A. 2020. A visually wrapped reference entry.", user_prompt)
        self.assertNotIn(r"A\nvisually", user_prompt)

    def test_invalid_entries_are_dropped_without_accepting_model_owned_ids(self) -> None:
        client = Mock()
        client.generate.return_value = {
            "references": [
                {
                    "id": "attacker-controlled",
                    "raw_text": "Valid source text.",
                    "title": "Valid title",
                    "authors": [],
                    "year": "not known",
                },
                {"raw_text": "Missing title", "authors": []},
                "not an object",
            ]
        }

        references = structure_reference_block("Valid source text.", client=client)

        self.assertEqual(len(references), 1)
        self.assertEqual(references[0]["id"], "ref-1")
        self.assertIsNone(references[0]["year"])

    def test_absent_section_never_calls_llm(self) -> None:
        client = Mock()

        references = extract_reference_list(
            [(1, "Abstract\nMethods\nResults\nConclusion")],
            client=client,
        )

        self.assertEqual(references, [])
        client.generate.assert_not_called()

    def test_provider_or_malformed_response_degrades_to_empty_list(self) -> None:
        for result in (RuntimeError("Groq unavailable"), {"wrong": []}):
            with self.subTest(result=result):
                client = Mock()
                if isinstance(result, Exception):
                    client.generate.side_effect = result
                else:
                    client.generate.return_value = result

                references = extract_reference_list(
                    [(1, "Research content\nReferences\n[1] A real citation.")],
                    client=client,
                )

                self.assertEqual(references, [])
                client.generate.assert_called_once()


class ReferencePersistenceTests(unittest.TestCase):
    def test_persist_ingestion_stores_reference_list_on_paper(self) -> None:
        paper_query = Mock()
        paper_query.insert.return_value.execute.return_value = SimpleNamespace(data=[])
        chunk_query = Mock()
        chunk_query.insert.return_value.execute.return_value = SimpleNamespace(data=[])
        database = Mock()
        database.table.side_effect = lambda name: (
            paper_query if name == "papers" else chunk_query
        )
        reference_list = [
            {
                "id": "ref-1",
                "index": 1,
                "raw_text": "A. Author. A cited work. 2020.",
                "title": "A cited work",
                "authors": ["A. Author"],
                "year": 2020,
            }
        ]

        with (
            patch.object(papers, "get_supabase", return_value=database),
            patch.object(papers, "upload_pdf"),
        ):
            papers._persist_ingestion(
                paper_id="paper-id",
                filename="paper.pdf",
                user_id="user-id",
                detected_domain="ml_cs",
                parent_paper_id=None,
                version_number=1,
                file_bytes=b"%PDF-test",
                parsed={"storage_path": "user-id/paper-id.pdf", "page_count": 1},
                chunks=[{"text": "body", "page_number": 1, "chunk_index": 0}],
                embeddings=[[0.0, 1.0]],
                reproducibility_signals={},
                reference_list=reference_list,
            )

        inserted_paper = paper_query.insert.call_args.args[0]
        self.assertEqual(inserted_paper["reference_list"], reference_list)

    def test_pre_migration_schema_compatibility_drops_only_reference_list(self) -> None:
        query = Mock()
        query.insert.return_value.execute.side_effect = [
            RuntimeError("Could not find the 'reference_list' column in the schema cache"),
            SimpleNamespace(data=[]),
        ]
        database = Mock()
        database.table.return_value = query
        row = {
            "id": "paper-id",
            "user_id": "user-id",
            "reference_list": [],
            "filename": "paper.pdf",
        }

        papers._insert_paper_compat(database, row)

        self.assertEqual(query.insert.call_count, 2)
        retried_row = query.insert.call_args_list[1].args[0]
        self.assertNotIn("reference_list", retried_row)
        self.assertEqual(retried_row["user_id"], "user-id")

    def test_migration_defines_non_null_json_array_default(self) -> None:
        migration = (
            Path(__file__).resolve().parents[1]
            / "migrations"
            / "005_citation_system.sql"
        ).read_text(encoding="utf-8").lower()

        self.assertIn("add column if not exists reference_list jsonb", migration)
        self.assertIn("alter column reference_list set default '[]'::jsonb", migration)
        self.assertIn("alter column reference_list set not null", migration)
        self.assertIn("jsonb_typeof(reference_list) = 'array'", migration)


if __name__ == "__main__":
    unittest.main()
