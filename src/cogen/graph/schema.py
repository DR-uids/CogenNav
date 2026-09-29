"""图谱数据模型与契约常量。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

GRAPH_SCHEMA_VERSION = 1

NodeKind = Literal[
    "repo",
    "dir",
    "file",
    "module",
    "package",
    "class",
    "interface",
    "struct",
    "enum",
    "trait",
    "function",
    "method",
    "variable",
    "constant",
    "type",
    "route",
    "test",
    "external",
    "community",
]

EdgeRelation = Literal[
    "contains",
    "defines",
    "imports",
    "calls",
    "extends",
    "implements",
    "instantiates",
    "references",
    "type_uses",
    "tests",
    "exposes",
    "returns",
]

Confidence = Literal["EXTRACTED", "INFERRED", "AMBIGUOUS"]

NODE_KINDS: tuple[str, ...] = (
    "repo",
    "dir",
    "file",
    "module",
    "package",
    "class",
    "interface",
    "struct",
    "enum",
    "trait",
    "function",
    "method",
    "variable",
    "constant",
    "type",
    "route",
    "test",
    "external",
    "community",
)

EDGE_RELATIONS: tuple[str, ...] = (
    "contains",
    "defines",
    "imports",
    "calls",
    "extends",
    "implements",
    "instantiates",
    "references",
    "type_uses",
    "tests",
    "exposes",
    "returns",
)

CONFIDENCE_LEVELS: tuple[str, ...] = ("EXTRACTED", "INFERRED", "AMBIGUOUS")

REPO_STATES: tuple[str, ...] = ("queued", "running", "done", "error")

# 流水线阶段（用于进度展示）
JOB_PHASES: tuple[str, ...] = (
    "resolve",
    "clone",
    "walk",
    "parse",
    "extract",
    "build",
    "analyze",
    "name",
    "done",
)


@dataclass(frozen=True)
class NodeRec:
    """图谱节点（M3 起写入）。"""

    id: str
    kind: NodeKind
    name: str
    qualified: str
    file: str | None = None
    sl: int = 0
    sc: int = 0
    el: int = 0
    ec: int = 0
    language: str | None = None
    community: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EdgeRec:
    """图谱边（M3 起写入）。"""

    src: str
    dst: str
    relation: EdgeRelation
    confidence: Confidence
    file: str | None = None
    sl: int = 0
    sc: int = 0
    el: int = 0
    ec: int = 0


@dataclass
class FileRecord:
    """一个被索引的文本文件。"""

    path: str
    language: str
    size: int
    loc: int
    sha256: str
    mtime: float
    parse_ok: bool = True
    error: str | None = None
    #: 解析阶段回填（见 store.update_parse_results）
    node_count: int = 0
    error_count: int = 0
    missing_count: int = 0


@dataclass
class SkippedFile:
    path: str
    reason: str


@dataclass
class RepoMeta:
    """仓库元信息（同时是 API 的 RepoSummary 来源）。"""

    repo_id: str
    target: str
    source: Literal["local", "git"]
    ref: str | None
    root_path: str
    state: str = "queued"
    file_count: int = 0
    loc: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    indexed_at: str | None = None
    message: str | None = None
    job_id: str | None = None
    commit: str | None = None

    def to_api(self) -> dict[str, Any]:
        """转成前端约定的 camelCase 结构。"""
        return {
            "repoId": self.repo_id,
            "target": self.target,
            "source": self.source,
            "ref": self.ref,
            "rootPath": self.root_path,
            "state": self.state,
            "fileCount": self.file_count,
            "loc": self.loc,
            "languages": self.languages,
            "indexedAt": self.indexed_at,
            "message": self.message,
            "jobId": self.job_id,
            "commit": self.commit,
        }
