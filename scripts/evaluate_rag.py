"""Evaluate all retrieval ablations on a fixed JSONL query/qrels dataset."""

import argparse
import json
import math
from pathlib import Path

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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True, type=Path)
    args = parser.parse_args()
    cases = [
        json.loads(line)
        for line in args.dataset.read_text(encoding="utf-8").splitlines()
        if line
    ]
    if not cases:
        raise ValueError("evaluation dataset is empty")
    pipeline = build_live_knowledge_pipeline()
    report = {}
    for mode in RetrievalMode:
        totals = {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0}
        for case in cases:
            documents, _ = pipeline.retrieve(case["query"], mode=mode)
            result = metrics(
                [document.chunk_id for document in documents],
                set(case["relevant_chunk_ids"]),
            )
            for name, value in result.items():
                totals[name] += value
        report[mode.value] = {
            name: value / len(cases) for name, value in totals.items()
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
