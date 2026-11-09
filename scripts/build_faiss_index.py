"""Build an aligned cosine-similarity FAISS index from a JSONL chunk manifest."""

import argparse
import json
import os
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from langchain_openai import OpenAIEmbeddings

from src.harness.rag import RetrievalDocument


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--index", required=True, type=Path)
    parser.add_argument("--documents", required=True, type=Path)
    args = parser.parse_args()
    load_dotenv(".env")
    try:
        import faiss
    except ImportError as exc:
        raise SystemExit("Install RAG dependencies with: poetry install -E rag") from exc

    chunks = _read_chunks(args.input)
    embeddings = OpenAIEmbeddings(
        model=os.getenv("EMBEDDING_MODEL", "text-embedding-3-small"),
        api_key=os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("EMBEDDING_API_BASE") or os.getenv("OPENAI_API_BASE"),
    )
    vectors = np.asarray(
        embeddings.embed_documents([chunk.content for chunk in chunks]),
        dtype="float32",
    )
    faiss.normalize_L2(vectors)
    index = faiss.IndexFlatIP(vectors.shape[1])
    index.add(vectors)

    args.index.parent.mkdir(parents=True, exist_ok=True)
    args.documents.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(args.index))
    with args.documents.open("w", encoding="utf-8") as output_file:
        for chunk in chunks:
            output_file.write(chunk.model_dump_json() + "\n")
    print(f"indexed {len(chunks)} chunks")


def _read_chunks(path: Path) -> list[RetrievalDocument]:
    chunks = []
    seen_ids = set()
    with path.open(encoding="utf-8") as input_file:
        for line_number, line in enumerate(input_file, start=1):
            if not line.strip():
                continue
            chunk = RetrievalDocument.model_validate(json.loads(line))
            if chunk.chunk_id in seen_ids:
                raise ValueError(f"duplicate chunk_id at line {line_number}")
            seen_ids.add(chunk.chunk_id)
            chunks.append(chunk)
    if not chunks:
        raise ValueError("input manifest is empty")
    return chunks


if __name__ == "__main__":
    main()
