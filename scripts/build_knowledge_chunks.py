"""Build a deterministic JSONL knowledge corpus from Markdown documents."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "section"


def chunk_markdown(path: Path, *, max_chars: int) -> list[dict]:
    source = path.as_posix()
    heading_path = [path.stem]
    blocks: list[tuple[tuple[str, ...], str]] = []
    paragraph: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            text = "\n".join(paragraph).strip()
            if text:
                blocks.append((tuple(heading_path), text))
            paragraph.clear()

    for line in path.read_text(encoding="utf-8").splitlines():
        heading = HEADING.match(line)
        if heading:
            flush_paragraph()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            heading_path[level - 1 :] = [title]
            continue
        if line.strip():
            paragraph.append(line.rstrip())
        else:
            flush_paragraph()
    flush_paragraph()

    chunks: list[dict] = []
    current_path: tuple[str, ...] = ()
    current_parts: list[str] = []
    section_counts: dict[str, int] = {}

    def flush_chunk() -> None:
        if not current_parts:
            return
        section_slug = slug("-".join(current_path))
        section_counts[section_slug] = section_counts.get(section_slug, 0) + 1
        chunks.append(
            {
                "chunk_id": (
                    f"{slug(path.stem)}-{section_slug}-"
                    f"{section_counts[section_slug]:02d}"
                ),
                "content": (
                    f"Title: {' > '.join(current_path)}\n"
                    + "\n\n".join(current_parts)
                ),
                "source": source,
                "metadata": {
                    "section": current_path[-1],
                    "heading_path": list(current_path),
                },
            }
        )
        current_parts.clear()

    for block_path, block_text in blocks:
        if current_parts and block_path != current_path:
            flush_chunk()
        current_path = block_path
        if current_parts and sum(len(part) for part in current_parts) + len(block_text) > max_chars:
            flush_chunk()
        current_parts.append(block_text)
    flush_chunk()
    return chunks


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--max-chars", type=int, default=900)
    args = parser.parse_args()
    if args.max_chars < 200:
        raise ValueError("max-chars must be at least 200")

    chunks = []
    seen_ids = set()
    for path in args.inputs:
        if not path.is_file():
            raise FileNotFoundError(path)
        for chunk in chunk_markdown(path, max_chars=args.max_chars):
            if chunk["chunk_id"] in seen_ids:
                raise ValueError(f"duplicate chunk_id: {chunk['chunk_id']}")
            seen_ids.add(chunk["chunk_id"])
            chunks.append(chunk)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(chunk, ensure_ascii=False) + "\n" for chunk in chunks),
        encoding="utf-8",
    )
    print(f"wrote {len(chunks)} chunks to {args.output}")


if __name__ == "__main__":
    main()
