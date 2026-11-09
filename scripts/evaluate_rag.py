"""Evaluate all retrieval ablations on a fixed JSONL query/qrels dataset."""

import argparse
import json
import math
import os
from pathlib import Path

from dotenv import load_dotenv

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from src.harness.rag import RetrievalMode
from src.harness.skills.knowledge_pipeline import build_live_knowledge_pipeline


def metrics(ranked_ids: list[str], relevant_ids: set[str]) -> dict[str, float]:
    hits = [1 if chunk_id in relevant_ids else 0 for chunk_id in ranked_ids]
    recall = sum(hits) / len(relevant_ids) if relevant_ids else 0.0
    reciprocal_rank = next(
        (1 / rank for rank, hit in enumerate(hits, start=1) if hit),
        0.0,
    )
    dcg = sum(hit / math.log2(rank + 1) for rank, hit in enumerate(hits, start=1))
    ideal_hits = [1] * min(len(relevant_ids), len(ranked_ids))
    ideal_dcg = sum(
        hit / math.log2(rank + 1)
        for rank, hit in enumerate(ideal_hits, start=1)
    )
    return {
        "recall": recall,
        "mrr": reciprocal_rank,
        "ndcg": dcg / ideal_dcg if ideal_dcg else 0.0,
    }


def diagnose_drops(
    relevant_ids: set[str],
    rankings: dict[str, list[str]],
) -> dict[str, list[str]]:
    faiss_hits = relevant_ids & set(rankings.get(RetrievalMode.FAISS.value, []))
    svm_hits = relevant_ids & set(rankings.get(RetrievalMode.FAISS_SVM.value, []))
    full_hits = relevant_ids & set(rankings.get(RetrievalMode.FULL.value, []))
    return {
        "missing_from_faiss_top_3": sorted(relevant_ids - faiss_hits),
        "dropped_by_svmrank": sorted(faiss_hits - svm_hits),
        "dropped_by_cross_encoder": sorted(svm_hits - full_hits),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--mode", choices=[mode.value for mode in RetrievalMode])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    load_dotenv(".env")
    os.environ.setdefault("FAISS_INDEX_PATH", "rag_data/knowledge.faiss")
    os.environ.setdefault("RAG_DOCUMENTS_PATH", "rag_data/documents.jsonl")
    os.environ.setdefault("SVMRANK_MODEL_PATH", "rag_data/svmrank_weights.json")
    os.environ["RAG_FAISS_TOP_K"] = "100"
    os.environ["RAG_LOAD_ALL_RERANKERS"] = "true"
    # Ranking quality and refusal quality are separate evaluations. Disabling
    # threshold filtering here keeps Cross-Encoder logits comparable by rank.
    os.environ["RAG_RELEVANCE_THRESHOLD"] = "-inf"
    cases = [
        json.loads(line)
        for line in args.dataset.read_text(encoding="utf-8").splitlines()
        if line
    ]
    if not cases:
        raise ValueError("evaluation dataset is empty")
    pipeline = build_live_knowledge_pipeline()
    modes = [RetrievalMode(args.mode)] if args.mode else list(RetrievalMode)
    totals = {
        mode.value: {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
        for mode in modes
    }
    candidate_recall_totals = {
        mode.value: 0.0
        for mode in modes
        if mode in {RetrievalMode.FAISS, RetrievalMode.FAISS_SVM}
    }
    latency_totals = {mode.value: {} for mode in modes}
    case_reports = []
    for case in cases:
        relevant_ids = set(case["relevant_chunk_ids"])
        rankings = {}
        mode_reports = {}
        for mode in modes:
            print(f"mode={mode.value} query={case['query']}", flush=True)
            candidate_mode = mode in {RetrievalMode.FAISS, RetrievalMode.FAISS_SVM}
            documents, trace = pipeline.retrieve(
                case["query"],
                mode=mode,
                result_limit=20 if candidate_mode else None,
            )
            for stage in trace:
                latency_totals[mode.value][stage.stage] = (
                    latency_totals[mode.value].get(stage.stage, 0.0)
                    + stage.duration_ms
                )
            candidate_ids = [document.chunk_id for document in documents]
            ranked_documents = documents[:3]
            ranked_ids = [document.chunk_id for document in ranked_documents]
            rankings[mode.value] = ranked_ids
            result = metrics(ranked_ids, relevant_ids)
            for name, value in result.items():
                totals[mode.value][name] += value
            mode_report = {
                "metrics": {
                    "recall_at_3": result["recall"],
                    "mrr_at_3": result["mrr"],
                    "ndcg_at_3": result["ndcg"],
                },
                "top_3": [
                    {
                        "rank": rank,
                        "chunk_id": document.chunk_id,
                        "score": document.score,
                        "relevant": document.chunk_id in relevant_ids,
                    }
                    for rank, document in enumerate(ranked_documents, start=1)
                ],
            }
            if candidate_mode:
                candidate_recall = (
                    len(relevant_ids & set(candidate_ids)) / len(relevant_ids)
                    if relevant_ids
                    else 0.0
                )
                candidate_recall_totals[mode.value] += candidate_recall
                mode_report["metrics"]["candidate_recall_at_20"] = candidate_recall
            mode_reports[mode.value] = mode_report
        case_reports.append(
            {
                "query": case["query"],
                "relevant_chunk_ids": sorted(relevant_ids),
                "modes": mode_reports,
                "diagnosis": diagnose_drops(relevant_ids, rankings),
            }
        )
    report = {
        "evaluation": {
            "cutoff": 3,
            "threshold_filtering": False,
            "case_count": len(cases),
        },
        "summary": {
            mode.value: {
                **{
                    f"{name}_at_3": value / len(cases)
                    for name, value in totals[mode.value].items()
                },
                **(
                    {
                        "candidate_recall_at_20": (
                            candidate_recall_totals[mode.value] / len(cases)
                        )
                    }
                    if mode.value in candidate_recall_totals
                    else {}
                ),
                "average_stage_latency_ms": {
                    stage: duration / len(cases)
                    for stage, duration in latency_totals[mode.value].items()
                },
            }
            for mode in modes
        },
        "cases": case_reports,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
