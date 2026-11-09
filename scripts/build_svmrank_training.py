"""Create RankSVM feature rows from labeled queries and a real FAISS index."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from pathlib import Path

import faiss
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings

from src.harness.rag import FaissCandidateRetriever, RetrievalDocument


def tokens(text: str) -> set[str]:
    return set(re.findall(r"[\w\u4e00-\u9fff]+", text.casefold()))


def features(query: str, document: RetrievalDocument) -> dict[str, float]:
    query_tokens = tokens(query)
    document_tokens = tokens(document.content)
    return {
        "dense_score": document.score,
        "token_overlap": len(query_tokens & document_tokens) / max(len(query_tokens), 1),
        "exact_phrase": float(query.casefold() in document.content.casefold()),
        "length_penalty": -math.log1p(len(document_tokens)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queries", required=True, type=Path)
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--documents", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--hard-negatives", type=int, default=10)
    args = parser.parse_args()
    if args.hard_negatives < 1:
        raise ValueError("hard-negatives must be positive")
    load_dotenv(".env")

    documents = [
        RetrievalDocument.model_validate_json(line)
        for line in args.documents.read_text(encoding="utf-8").splitlines()
        if line
    ]
    document_ids = {document.chunk_id for document in documents}
    index = faiss.read_index(str(args.index))
    if index.ntotal != len(documents):
        raise ValueError("FAISS index and document manifest are not aligned")

    embeddings = OpenAIEmbeddings(
        model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        api_key=os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("EMBEDDING_API_BASE") or os.getenv("OPENAI_API_BASE"),
    )
    retriever = FaissCandidateRetriever(
        index=index,
        documents=documents,
        embed_query=embeddings.embed_query,
    )

    rows = []
    for line in args.queries.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        case = json.loads(line)
        relevant_ids = set(case["relevant_chunk_ids"])
        unknown_ids = relevant_ids - document_ids
        if unknown_ids:
            raise ValueError(f"unknown relevant chunk ids: {sorted(unknown_ids)}")
        ranked = retriever.retrieve(case["query"], len(documents))
        positives = [item for item in ranked if item.chunk_id in relevant_ids]
        hard_negatives = [
            item for item in ranked if item.chunk_id not in relevant_ids
        ][: args.hard_negatives]
        for document in positives + hard_negatives:
            rows.append(
                {
                    "query_id": case["query_id"],
                    "chunk_id": document.chunk_id,
                    "relevance": int(document.chunk_id in relevant_ids),
                    **features(case["query"], document),
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    print(f"wrote {len(rows)} feature rows to {args.output}")


if __name__ == "__main__":
    main()
