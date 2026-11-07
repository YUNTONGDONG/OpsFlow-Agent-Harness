import tempfile
import unittest
from pathlib import Path

from src.harness import (
    LocalWorkspace,
    WorkspaceArtifactRef,
)


class WorkspaceTest(unittest.TestCase):
    def test_local_workspace_round_trip_and_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = LocalWorkspace(directory)
            reference = workspace.write_json(
                "task-1",
                "steps/search/output.json",
                {"evidence": ["E102", "runbook"]},
            )

            loaded = workspace.read_json(reference)

            self.assertEqual(loaded, {"evidence": ["E102", "runbook"]})
            self.assertTrue(reference.uri.startswith("workspace://task-1/"))
            self.assertGreater(reference.size_bytes, 0)

    def test_local_workspace_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = LocalWorkspace(directory)

            with self.assertRaisesRegex(ValueError, "contained"):
                workspace.write_json("task-1", "../secret.json", {})

    def test_modified_artifact_fails_checksum_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = LocalWorkspace(directory)
            reference = workspace.write_json("task-1", "output.json", {"ok": True})
            path = Path(directory) / "task-1" / "output.json"
            path.write_text('{"ok":false}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "checksum|size"):
                workspace.read_json(
                    WorkspaceArtifactRef.model_validate(reference)
                )


if __name__ == "__main__":
    unittest.main()
