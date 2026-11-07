"""Three-stage retrieval pipeline used by the Knowledge Skill."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from time import perf_counter
from typing import Any, Protocol
import json
import math
import re

from pydantic import BaseModel, Field


class RetrievalDocument(BaseModel):
    chunk_id: str
    content: str
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    score: float = 0.0


class RetrievalStageMetrics(BaseModel):
    stage: str
    candidate_count: int
    duration_ms: float


class RAGResult(BaseModel):
    answer: str
    sources: list[RetrievalDocument]
    retrieval_trace: list[RetrievalStageMetrics]
    refused: bool = False
    model: str | None = None
    token_usage: dict[str, int] | None = None


class GeneratedAnswer(BaseModel):
    text: str
    model: str | None = None
    token_usage: dict[str, int] | None = None


class RetrievalMode(str, Enum):
    FAISS = "faiss"
    FAISS_SVM = "faiss_svm"
    FAISS_CROSS_ENCODER = "faiss_cross_encoder"
    FULL = "full"


class CandidateRetriever(Protocol):
    def retrieve(self, query: str, limit: int) -> list[RetrievalDocument]: ...


class CandidateRanker(Protocol):
    def rank(
        self,
        query: str,
        documents: Sequence[RetrievalDocument],
        limit: int,
    ) -> list[RetrievalDocument]: ...


class EvidenceAnswerer(Protocol):
    def answer(
        self,
        query: str,
        documents: Sequence[RetrievalDocument],
    ) -> str | GeneratedAnswer: ...


class ThreeStageRAGPipeline:
    """FAISS recall -> SVMRank -> Cross-Encoder -> grounded answer."""

    def __init__(
        self,
        *,
        retriever: CandidateRetriever,
        svm_ranker: CandidateRanker,
        cross_encoder_ranker: CandidateRanker,
        answerer: EvidenceAnswerer,
        recall_limit: int = 100,
        svm_limit: int = 20,
        final_limit: int = 3,
        relevance_threshold: float = 0.0,
        refusal_answer: str = "抱歉，我在知识库中没有找到足够相关的证据。",
    ) -> None:
        if not (recall_limit >= svm_limit >= final_limit > 0):
            raise ValueError("RAG limits must satisfy recall >= svm >= final > 0")
        self._retriever = retriever
        self._svm_ranker = svm_ranker
        self._cross_encoder_ranker = cross_encoder_ranker
        self._answerer = answerer
        self._recall_limit = recall_limit
        self._svm_limit = svm_limit
        self._final_limit = final_limit
        self._relevance_threshold = relevance_threshold
        self._refusal_answer = refusal_answer

    def search(self, query: str) -> RAGResult:
        selected, trace = self.retrieve(query, mode=RetrievalMode.FULL)
        if not selected:
            return RAGResult(
                answer=self._refusal_answer,
                sources=[],
                retrieval_trace=trace,
                refused=True,
            )

        started_at = perf_counter()
        generated = self._answerer.answer(query, selected)
        trace.append(
            RetrievalStageMetrics(
                stage="answer_generation",
                candidate_count=len(selected),
                duration_ms=(perf_counter() - started_at) * 1000,
            )
        )
        if isinstance(generated, GeneratedAnswer):
            return RAGResult(
                answer=generated.text,
                sources=selected,
                retrieval_trace=trace,
                model=generated.model,
                token_usage=generated.token_usage,
            )
        return RAGResult(answer=generated, sources=selected, retrieval_trace=trace)

    def retrieve(
        self,
        query: str,
        *,
        mode: RetrievalMode = RetrievalMode.FULL,
    ) -> tuple[list[RetrievalDocument], list[RetrievalStageMetrics]]:
        """Run retrieval only, including the four supported ablation modes."""

        trace: list[RetrievalStageMetrics] = []
        recalled = self._timed_stage(
            trace,
            "faiss",
            lambda: self._retriever.retrieve(query, self._recall_limit),
        )
        if mode == RetrievalMode.FAISS:
            selected = recalled[: self._final_limit]
        elif mode == RetrievalMode.FAISS_SVM:
            svm_ranked = self._timed_stage(
                trace,
                "svm_rank",
                lambda: self._svm_ranker.rank(query, recalled, self._svm_limit),
            )
            selected = svm_ranked[: self._final_limit]
        elif mode == RetrievalMode.FAISS_CROSS_ENCODER:
            selected = self._timed_stage(
                trace,
                "cross_encoder",
                lambda: self._cross_encoder_ranker.rank(
                    query,
                    recalled,
                    self._final_limit,
                ),
            )
        else:
            svm_ranked = self._timed_stage(
                trace,
                "svm_rank",
                lambda: self._svm_ranker.rank(query, recalled, self._svm_limit),
            )
            selected = self._timed_stage(
                trace,
                "cross_encoder",
                lambda: self._cross_encoder_ranker.rank(
                    query,
                    svm_ranked,
                    self._final_limit,
                ),
            )
        selected = [
            document
            for document in selected
            if document.score >= self._relevance_threshold
        ]
        return selected, trace

    @staticmethod
    def _timed_stage(
        trace: list[RetrievalStageMetrics],
        stage: str,
        operation: Callable[[], list[RetrievalDocument]],
    ) -> list[RetrievalDocument]:
        started_at = perf_counter()
        documents = operation()
        trace.append(
            RetrievalStageMetrics(
                stage=stage,
                candidate_count=len(documents),
                duration_ms=(perf_counter() - started_at) * 1000,
            )
        )
        return documents


class FaissCandidateRetriever:
    """Adapter around a loaded FAISS index and aligned chunk metadata."""

    def __init__(
        self,
        *,
        index: Any,
        documents: Sequence[RetrievalDocument],
        embed_query: Callable[[str], Sequence[float]],
    ) -> None:
        self._index = index
        self._documents = list(documents)
        self._embed_query = embed_query

    def retrieve(self, query: str, limit: int) -> list[RetrievalDocument]:
        import numpy as np

        vector = np.asarray([self._embed_query(query)], dtype="float32")
        norm = float(np.linalg.norm(vector[0]))
        if norm == 0:
            raise ValueError("Query embedding must not be a zero vector")
        vector /= norm
        scores, indices = self._index.search(vector, min(limit, len(self._documents)))
        results = []
        for score, index in zip(scores[0], indices[0]):
            if index < 0:
                continue
            results.append(
                self._documents[int(index)].model_copy(update={"score": float(score)})
            )
        return results


@dataclass(frozen=True)
class SVMRankWeights:
    dense_score: float
    token_overlap: float
    exact_phrase: float
    length_penalty: float
    intercept: float = 0.0

    @classmethod
    def from_json(cls, path: str | Path) -> "SVMRankWeights":
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))


class LinearSVMRanker:
    """Inference for a trained linear ranking SVM over inexpensive features."""

    def __init__(self, weights: SVMRankWeights) -> None:
        self._weights = weights

    def rank(
        self,
        query: str,
        documents: Sequence[RetrievalDocument],
        limit: int,
    ) -> list[RetrievalDocument]:
        query_tokens = _tokens(query)
        ranked = []
        for document in documents:
            document_tokens = _tokens(document.content)
            overlap = len(query_tokens & document_tokens) / max(len(query_tokens), 1)
            exact_phrase = float(query.casefold() in document.content.casefold())
            length_penalty = math.log1p(len(document_tokens))
            score = (
                self._weights.intercept
                + self._weights.dense_score * document.score
                + self._weights.token_overlap * overlap
                + self._weights.exact_phrase * exact_phrase
                + self._weights.length_penalty * length_penalty
            )
            ranked.append(document.model_copy(update={"score": score}))
        return sorted(ranked, key=lambda item: item.score, reverse=True)[:limit]


class CrossEncoderRanker:
    """Batch query-document scoring adapter for sentence-transformers models."""

    def __init__(self, model: Any) -> None:
        self._model = model

    def rank(
        self,
        query: str,
        documents: Sequence[RetrievalDocument],
        limit: int,
    ) -> list[RetrievalDocument]:
        if not documents:
            return []
        pairs = [(query, document.content) for document in documents]
        scores = self._model.predict(pairs)
        ranked = [
            document.model_copy(update={"score": float(score)})
            for document, score in zip(documents, scores)
        ]
        return sorted(ranked, key=lambda item: item.score, reverse=True)[:limit]


class CallableEvidenceAnswerer:
    def __init__(
        self,
        generate: Callable[
            [str, Sequence[RetrievalDocument]],
            str | GeneratedAnswer,
        ],
    ) -> None:
        self._generate = generate

    def answer(
        self,
        query: str,
        documents: Sequence[RetrievalDocument],
    ) -> str | GeneratedAnswer:
        return self._generate(query, documents)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w\u4e00-\u9fff]+", text.casefold()))
