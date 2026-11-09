"""Compare Cross-Encoder quality and latency across FAISS candidate windows."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from scripts.evaluate_rag import metrics
from src.harness.rag import RetrievalMode
from src.harness.skills.knowledge_pipeline import build_live_knowledge_pipeline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--windows", nargs="+", type=int, default=[5, 10, 20, 50])
    parser.add_argument("--model")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if any(window < 3 for window in args.windows):
        raise ValueError("rerank windows must be at least 3")

    load_dotenv(".env")
    os.environ.setdefault("FAISS_INDEX_PATH", "rag_data/knowledge.faiss")
    os.environ.setdefault("RAG_DOCUMENTS_PATH", "rag_data/documents.jsonl")
    os.environ.setdefault("SVMRANK_MODEL_PATH", "rag_data/svmrank_weights.json")
    os.environ["RAG_FAISS_TOP_K"] = "100"
    os.environ["RAG_RELEVANCE_THRESHOLD"] = "-inf"
    if args.model:
        os.environ["CROSS_ENCODER_MODEL"] = args.model

    cases = [
        json.loads(line)
        for line in args.dataset.read_text(encoding="utf-8").splitlines()
        if line
    ]
    if not cases:
        raise ValueError("evaluation dataset is empty")

    pipeline = build_live_knowledge_pipeline()
    report = {
        "case_count": len(cases),
        "model": os.getenv(
            "CROSS_ENCODER_MODEL",
            "cross-encoder/ms-marco-MiniLM-L-6-v2",
        ),
        "windows": {},
    }
    for window in args.windows:
        totals = {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
        latency_ms = 0.0
        case_results = []
        for case in cases:
            documents, trace = pipeline.retrieve(
                case["query"],
                mode=RetrievalMode.FAISS_CROSS_ENCODER,
                recall_limit=window,
            )
            relevant_ids = set(case["relevant_chunk_ids"])
            result = metrics(
                [document.chunk_id for document in documents],
                relevant_ids,
            )
            for name, value in result.items():
                totals[name] += value
            cross_encoder_stage = next(
                item for item in trace if item.stage == "cross_encoder"
            )
            latency_ms += cross_encoder_stage.duration_ms
            case_results.append(
                {
                    "query": case["query"],
                    "ranked_chunk_ids": [item.chunk_id for item in documents],
                    "metrics": result,
                    "cross_encoder_latency_ms": cross_encoder_stage.duration_ms,
                }
            )
        report["windows"][str(window)] = {
            "recall_at_3": totals["recall"] / len(cases),
            "mrr_at_3": totals["mrr"] / len(cases),
            "ndcg_at_3": totals["ndcg"] / len(cases),
            "average_cross_encoder_latency_ms": latency_ms / len(cases),
            "cases": case_results,
        }

    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
