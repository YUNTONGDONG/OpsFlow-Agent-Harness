"""Run one live RAG query and save the complete result as JSON evidence."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("VECLIB_MAXIMUM_THREADS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from src.harness.skills.knowledge_pipeline import build_live_knowledge_pipeline


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--threshold",
        type=float,
        help="Experimental Cross-Encoder logit threshold; calibrate before production use.",
    )
    args = parser.parse_args()
    load_dotenv(".env")
    os.environ.setdefault("FAISS_INDEX_PATH", "rag_data/knowledge.faiss")
    os.environ.setdefault("RAG_DOCUMENTS_PATH", "rag_data/documents.jsonl")
    os.environ.setdefault("SVMRANK_MODEL_PATH", "rag_data/svmrank_weights.json")
    if args.threshold is not None:
        os.environ["RAG_RELEVANCE_THRESHOLD"] = str(args.threshold)

    result = build_live_knowledge_pipeline().search(args.query)
    rendered = result.model_dump_json(indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
