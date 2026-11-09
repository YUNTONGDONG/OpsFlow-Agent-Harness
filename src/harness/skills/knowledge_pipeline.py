"""Lazy production composition for the Knowledge Skill."""

import json
import os
from pathlib import Path
from typing import Sequence

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from ..rag import (
    CallableEvidenceAnswerer,
    CrossEncoderRanker,
    FaissCandidateRetriever,
    GeneratedAnswer,
    LinearSVMRanker,
    RetrievalDocument,
    RetrievalMode,
    SVMRankWeights,
    ThreeStageRAGPipeline,
)


def build_live_knowledge_pipeline() -> ThreeStageRAGPipeline:
    search_mode = RetrievalMode(
        os.getenv("RAG_RETRIEVAL_MODE", "faiss_cross_encoder")
    )
    load_all_rerankers = (
        os.getenv("RAG_LOAD_ALL_RERANKERS", "false").casefold() == "true"
    )
    try:
        import faiss
    except ImportError as exc:
        raise RuntimeError(
            "Install the rag dependency group before using knowledge_search"
        ) from exc

    if load_all_rerankers or search_mode in {
        RetrievalMode.FAISS_CROSS_ENCODER,
        RetrievalMode.FULL,
    }:
        try:
            from sentence_transformers import CrossEncoder
        except ImportError as exc:
            raise RuntimeError(
                "Install the rag dependency group before enabling reranking"
            ) from exc
        cross_encoder_ranker = CrossEncoderRanker(
            CrossEncoder(
                os.getenv(
                    "CROSS_ENCODER_MODEL",
                    "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
                ),
                revision=os.getenv("CROSS_ENCODER_REVISION") or None,
                device=os.getenv("CROSS_ENCODER_DEVICE", "cpu"),
            )
        )
    else:
        cross_encoder_ranker = LinearSVMRanker(
            SVMRankWeights(0, 0, 0, 0)
        )

    index_path = _required_path("FAISS_INDEX_PATH")
    documents_path = _required_path("RAG_DOCUMENTS_PATH")
    documents = _load_documents(documents_path)
    index = faiss.read_index(str(index_path))
    if index.ntotal != len(documents):
        raise ValueError("FAISS index and document manifest are not aligned")

    embeddings = OpenAIEmbeddings(
        model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        api_key=os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("EMBEDDING_API_BASE") or os.getenv("OPENAI_API_BASE"),
    )
    if load_all_rerankers or search_mode in {
        RetrievalMode.FAISS_SVM,
        RetrievalMode.FULL,
    }:
        svm_ranker = LinearSVMRanker(
            SVMRankWeights.from_json(_required_path("SVMRANK_MODEL_PATH"))
        )
    else:
        svm_ranker = LinearSVMRanker(SVMRankWeights(0, 0, 0, 0))
    llm = ChatOpenAI(
        model=os.getenv("BASE_MODEL", "gpt-4o-mini"),
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_API_BASE"),
        temperature=0,
    )

    def generate(
        query: str,
        evidence: Sequence[RetrievalDocument],
    ) -> GeneratedAnswer:
        context = "\n\n".join(
            f"[{item.chunk_id}] source={item.source}\n{item.content}"
            for item in evidence
        )
        response = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "只根据提供的证据回答。每个结论使用 [chunk_id] 标注来源；"
                        "证据不足时明确拒答，不得使用外部知识。\n\n证据：\n"
                        + context
                    )
                ),
                HumanMessage(content=query),
            ]
        )
        usage = response.usage_metadata or {}
        token_usage = {
            key: int(value)
            for key, value in usage.items()
            if isinstance(value, int)
        }
        return GeneratedAnswer(
            text=str(response.content),
            model=os.getenv("BASE_MODEL", "gpt-4o-mini"),
            token_usage=token_usage or None,
        )

    return ThreeStageRAGPipeline(
        retriever=FaissCandidateRetriever(
            index=index,
            documents=documents,
            embed_query=embeddings.embed_query,
        ),
        svm_ranker=svm_ranker,
        cross_encoder_ranker=cross_encoder_ranker,
        answerer=CallableEvidenceAnswerer(generate),
        recall_limit=int(os.getenv("RAG_FAISS_TOP_K", "5")),
        svm_limit=int(os.getenv("RAG_SVM_TOP_K", "20")),
        final_limit=int(os.getenv("RAG_FINAL_TOP_K", "3")),
        relevance_threshold=float(os.getenv("RAG_RELEVANCE_THRESHOLD", "0")),
        search_mode=search_mode,
    )


def _required_path(name: str) -> Path:
    value = os.getenv(name)
    if not value:
        raise ValueError(f"{name} is required")
    path = Path(value)
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _load_documents(path: Path) -> list[RetrievalDocument]:
    documents = []
    with path.open(encoding="utf-8") as source_file:
        for line in source_file:
            if line.strip():
                documents.append(RetrievalDocument.model_validate(json.loads(line)))
    if not documents:
        raise ValueError("RAG document manifest is empty")
    return documents
