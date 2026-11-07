import unittest
import os
import subprocess
import sys
from unittest.mock import patch

from pydantic import ValidationError

from src.harness import InMemoryTraceSink, SkillRegistry, SkillRuntime
from src.harness.rag import (
    CallableEvidenceAnswerer,
    RetrievalDocument,
    ThreeStageRAGPipeline,
)
from src.harness.skills import KnowledgeSearchSkill


class FixedRetriever:
    def retrieve(self, query, limit):
        return [
            RetrievalDocument(
                chunk_id="e102-runbook",
                source="runbook.md",
                content="Restart the worker.",
                score=1,
            )
        ]


class PassThroughRanker:
    def rank(self, query, documents, limit):
        return list(documents[:limit])


class KnowledgeSearchSkillTest(unittest.TestCase):
    def test_knowledge_tool_import_does_not_require_unrelated_credentials(self) -> None:
        environment = os.environ.copy()
        for key in (
            "SERPAPI_API_KEY",
            "SLACK_BOT_TOKEN",
            "SLACK_APP_TOKEN",
        ):
            environment.pop(key, None)

        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "from src.Tools import get_info_from_local; "
                "print(get_info_from_local.name)",
            ],
            cwd=os.getcwd(),
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "get_info_from_local")

    def test_valid_query_uses_injected_search_backend(self) -> None:
        skill = KnowledgeSearchSkill(
            search_fn=lambda query: f"evidence for: {query}"
        )

        arguments = skill.validate_input({"query": "How do I handle E102?"})
        result = skill.execute(arguments)

        self.assertTrue(result.success)
        self.assertEqual(
            result.data,
            {
                "answer": "evidence for: How do I handle E102?",
                "sources": [],
                "retrieval_trace": [],
                "refused": False,
                "model": None,
                "token_usage": None,
            },
        )

    def test_empty_query_is_rejected_by_schema(self) -> None:
        skill = KnowledgeSearchSkill(search_fn=lambda query: query)

        with self.assertRaises(ValidationError):
            skill.validate_input({"query": ""})

    def test_backend_failure_returns_normalized_error(self) -> None:
        def failing_backend(query: str) -> str:
            raise RuntimeError("backend unavailable")

        skill = KnowledgeSearchSkill(search_fn=failing_backend)
        arguments = skill.validate_input({"query": "E102"})
        result = skill.execute(arguments)

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, "KNOWLEDGE_SEARCH_FAILED")
        self.assertNotIn("backend unavailable", result.message or "")

    def test_retrieval_stage_metrics_are_added_to_skill_trace(self) -> None:
        pipeline = ThreeStageRAGPipeline(
            retriever=FixedRetriever(),
            svm_ranker=PassThroughRanker(),
            cross_encoder_ranker=PassThroughRanker(),
            answerer=CallableEvidenceAnswerer(
                lambda query, evidence: "Restart the worker [e102-runbook]"
            ),
        )
        skill = KnowledgeSearchSkill(pipeline=pipeline)
        registry = SkillRegistry()
        registry.register(skill)
        trace = InMemoryTraceSink()
        runtime = SkillRuntime(registry, trace_sink=trace)

        result = runtime.execute(
            "knowledge_search",
            {"query": "E102"},
            request_id="rag-trace",
        )

        self.assertTrue(result.success)
        success_event = trace.events_for("rag-trace")[-1]
        stages = success_event.attributes["retrieval_stages"]
        self.assertEqual(
            [item["stage"] for item in stages],
            ["faiss", "svm_rank", "cross_encoder", "answer_generation"],
        )
        self.assertEqual(result.data["sources"][0]["chunk_id"], "e102-runbook")

    def test_default_pipeline_is_loaded_once_and_reused(self) -> None:
        pipeline = ThreeStageRAGPipeline(
            retriever=FixedRetriever(),
            svm_ranker=PassThroughRanker(),
            cross_encoder_ranker=PassThroughRanker(),
            answerer=CallableEvidenceAnswerer(
                lambda query, evidence: "answer [e102-runbook]"
            ),
        )
        skill = KnowledgeSearchSkill()

        with patch(
            "src.harness.skills.knowledge_search._build_default_pipeline",
            return_value=pipeline,
        ) as build:
            first = skill.execute(skill.validate_input({"query": "E102"}))
            second = skill.execute(skill.validate_input({"query": "E102 again"}))

        self.assertTrue(first.success)
        self.assertTrue(second.success)
        build.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
