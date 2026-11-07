"""Knowledge-base search through the canonical three-stage RAG pipeline."""

from collections.abc import Callable
from threading import Lock

from pydantic import BaseModel, Field

from ..rag import RAGResult, RetrievalDocument, RetrievalStageMetrics, ThreeStageRAGPipeline
from ..skill import BaseSkill, RetryPolicy, SkillOperationType, SkillResult


class KnowledgeSearchInput(BaseModel):
    query: str = Field(min_length=1, max_length=1000)


class KnowledgeSearchOutput(BaseModel):
    answer: str
    sources: list[RetrievalDocument] = Field(default_factory=list)
    retrieval_trace: list[RetrievalStageMetrics] = Field(default_factory=list)
    refused: bool = False
    model: str | None = None
    token_usage: dict[str, int] | None = None


def _build_default_pipeline() -> ThreeStageRAGPipeline:
    # Keep heavy model and index initialization out of registry discovery.
    from .knowledge_pipeline import build_live_knowledge_pipeline

    return build_live_knowledge_pipeline()


class KnowledgeSearchSkill(BaseSkill[KnowledgeSearchInput]):
    name = "knowledge_search"
    description = "Search the internal knowledge base and answer from retrieved evidence."
    input_schema = KnowledgeSearchInput
    output_schema = KnowledgeSearchOutput
    operation_type = SkillOperationType.READ
    retry_policy = RetryPolicy(max_attempts=2, backoff_seconds=0.5)
    timeout_seconds = 30
    concurrency_limit = 4

    def __init__(
        self,
        search_fn: Callable[[str], str] | None = None,
        pipeline: ThreeStageRAGPipeline | None = None,
    ) -> None:
        if search_fn is not None and pipeline is not None:
            raise ValueError("Provide either search_fn or pipeline, not both")
        self._search_fn = search_fn
        self._pipeline = pipeline
        self._pipeline_lock = Lock()

    def execute(self, arguments: KnowledgeSearchInput) -> SkillResult:
        try:
            if self._search_fn is not None:
                result = RAGResult(
                    answer=self._search_fn(arguments.query),
                    sources=[],
                    retrieval_trace=[],
                )
            else:
                result = self._get_pipeline().search(arguments.query)
            trace = [item.model_dump(mode="json") for item in result.retrieval_trace]
            return SkillResult(
                success=True,
                data=result.model_dump(mode="json"),
                trace_attributes={
                    "retrieval_stages": trace,
                    "model": result.model,
                    "token_usage": result.token_usage,
                },
            )
        except Exception:
            return SkillResult(
                success=False,
                error_code="KNOWLEDGE_SEARCH_FAILED",
                message="The knowledge base could not be queried.",
            )

    def _get_pipeline(self) -> ThreeStageRAGPipeline:
        if self._pipeline is not None:
            return self._pipeline
        with self._pipeline_lock:
            if self._pipeline is None:
                self._pipeline = _build_default_pipeline()
        return self._pipeline
