"""Durable task artifacts separated from short-lived Redis state."""

from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, Protocol

from pydantic import BaseModel


class WorkspaceArtifactRef(BaseModel):
    uri: str
    sha256: str
    size_bytes: int
    media_type: str = "application/json"


class Workspace(Protocol):
    def write_json(
        self,
        task_id: str,
        relative_path: str,
        value: Any,
    ) -> WorkspaceArtifactRef: ...

    def read_json(self, reference: WorkspaceArtifactRef) -> Any: ...


class InMemoryWorkspace:
    def __init__(self) -> None:
        self._artifacts: dict[str, bytes] = {}

    def write_json(
        self,
        task_id: str,
        relative_path: str,
        value: Any,
    ) -> WorkspaceArtifactRef:
        uri = _workspace_uri(task_id, relative_path)
        content = _json_bytes(value)
        self._artifacts[uri] = content
        return _reference(uri, content)

    def read_json(self, reference: WorkspaceArtifactRef) -> Any:
        try:
            content = self._artifacts[reference.uri]
        except KeyError as exc:
            raise FileNotFoundError(reference.uri) from exc
        _verify_content(reference, content)
        return json.loads(content)


class LocalWorkspace:
    def __init__(self, root: str | Path = "workspaces") -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def write_json(
        self,
        task_id: str,
        relative_path: str,
        value: Any,
    ) -> WorkspaceArtifactRef:
        uri = _workspace_uri(task_id, relative_path)
        path = self._path_for_uri(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = _json_bytes(value)
        with NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            delete=False,
        ) as temporary_file:
            temporary_file.write(content)
            temporary_path = Path(temporary_file.name)
        os.replace(temporary_path, path)
        return _reference(uri, content)

    def read_json(self, reference: WorkspaceArtifactRef) -> Any:
        content = self._path_for_uri(reference.uri).read_bytes()
        _verify_content(reference, content)
        return json.loads(content)

    def _path_for_uri(self, uri: str) -> Path:
        prefix = "workspace://"
        if not uri.startswith(prefix):
            raise ValueError("Workspace URI must start with workspace://")
        relative = uri.removeprefix(prefix)
        path = (self._root / relative).resolve()
        if self._root not in path.parents:
            raise ValueError("Workspace path escapes configured root")
        return path


def _workspace_uri(task_id: str, relative_path: str) -> str:
    task_path = Path(task_id)
    artifact_path = Path(relative_path)
    if (
        not task_id
        or task_path.is_absolute()
        or artifact_path.is_absolute()
        or ".." in task_path.parts
        or ".." in artifact_path.parts
    ):
        raise ValueError("Workspace paths must be relative and contained")
    return f"workspace://{task_id}/{artifact_path.as_posix()}"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _reference(uri: str, content: bytes) -> WorkspaceArtifactRef:
    return WorkspaceArtifactRef(
        uri=uri,
        sha256=sha256(content).hexdigest(),
        size_bytes=len(content),
    )


def _verify_content(reference: WorkspaceArtifactRef, content: bytes) -> None:
    if len(content) != reference.size_bytes:
        raise ValueError("Workspace artifact size does not match reference")
    if sha256(content).hexdigest() != reference.sha256:
        raise ValueError("Workspace artifact checksum does not match reference")
