import tempfile
import unittest
from pathlib import Path

from scripts.build_knowledge_chunks import chunk_markdown


class BuildKnowledgeChunksTest(unittest.TestCase):
    def test_chunks_are_stable_and_keep_source_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Guide.md"
            path.write_text(
                "# Guide\n\nIntro paragraph.\n\n## Recovery\n\nResume safely.\n",
                encoding="utf-8",
            )

            chunks = chunk_markdown(path, max_chars=200)

        self.assertEqual(
            [item["chunk_id"] for item in chunks],
            ["guide-guide-01", "guide-guide-recovery-01"],
        )
        self.assertEqual(chunks[0]["source"], path.as_posix())
        self.assertNotIn("Resume safely", chunks[0]["content"])
        self.assertIn("Title: Guide > Recovery", chunks[1]["content"])
        self.assertEqual(chunks[1]["metadata"]["section"], "Recovery")
        self.assertEqual(chunks[1]["metadata"]["heading_path"], ["Guide", "Recovery"])


if __name__ == "__main__":
    unittest.main()
