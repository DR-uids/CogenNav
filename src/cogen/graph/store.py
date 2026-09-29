"""SQLite 存储：一仓一库（``.cogen/db/<repoId>.sqlite``）。

M1 只用到 meta / files 两张表；nodes / edges / communities / symbol_fts 的 DDL 一并建好，
M3 直接写入，避免中途迁移。写入由单锁串行化，读操作在 WAL 下可并发。
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from ..config import Settings
from .analyze import CommunityInfo
from .schema import GRAPH_SCHEMA_VERSION, EdgeRec, FileRecord, NodeRec, RepoMeta

if TYPE_CHECKING:  # 仅在类型检查时依赖解析器，避免 store 与原生语法库耦合
    from ..parse.parser import ParseOutcome

_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS files (
    path      TEXT PRIMARY KEY,
    language  TEXT NOT NULL,
    size      INTEGER NOT NULL,
    loc       INTEGER NOT NULL,
    sha256    TEXT NOT NULL,
    mtime     REAL NOT NULL,
    parse_ok  INTEGER NOT NULL DEFAULT 1,
    error     TEXT,
    node_count    INTEGER NOT NULL DEFAULT 0,
    error_count   INTEGER NOT NULL DEFAULT 0,
    missing_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS nodes (
    id        TEXT PRIMARY KEY,
    kind      TEXT NOT NULL,
    name      TEXT NOT NULL,
    qualified TEXT NOT NULL,
    file      TEXT,
    sl INTEGER NOT NULL DEFAULT 0,
    sc INTEGER NOT NULL DEFAULT 0,
    el INTEGER NOT NULL DEFAULT 0,
    ec INTEGER NOT NULL DEFAULT 0,
    language  TEXT,
    community INTEGER,
    extra     TEXT
);
CREATE INDEX IF NOT EXISTS nodes_file ON nodes(file);
CREATE INDEX IF NOT EXISTS nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS nodes_community ON nodes(community);
CREATE INDEX IF NOT EXISTS nodes_qualified ON nodes(qualified);
CREATE TABLE IF NOT EXISTS edges (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    src        TEXT NOT NULL,
    dst        TEXT NOT NULL,
    relation   TEXT NOT NULL,
    confidence TEXT NOT NULL,
    file       TEXT,
    sl INTEGER NOT NULL DEFAULT 0,
    sc INTEGER NOT NULL DEFAULT 0,
    el INTEGER NOT NULL DEFAULT 0,
    ec INTEGER NOT NULL DEFAULT 0,
    UNIQUE (src, dst, relation, file, sl, sc)
);
CREATE INDEX IF NOT EXISTS edges_src ON edges(src);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst);
CREATE INDEX IF NOT EXISTS edges_relation ON edges(relation);
CREATE TABLE IF NOT EXISTS communities (
    id           INTEGER PRIMARY KEY,
    name         TEXT,
    summary      TEXT,
    size         INTEGER NOT NULL DEFAULT 0,
    cohesion     REAL,
    named_by     TEXT,
    content_hash TEXT
);
CREATE TABLE IF NOT EXISTS extractions (
    path       TEXT PRIMARY KEY,
    sha256     TEXT NOT NULL,
    payload    TEXT NOT NULL,
    updated_at TEXT
);
CREATE TABLE IF NOT EXISTS naming_cache (
    content_hash TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    summary      TEXT,
    named_by     TEXT NOT NULL,
    updated_at   TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS symbol_fts USING fts5 (
    name, qualified, file, doc, snippet
);
"""


class Store:
    """单个仓库的数据库句柄。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self.initialize()

    # ── 生命周期 ────────────────────────────────────────────────────
    #: 旧库缺列时按此补齐（一仓一库，改动很小，不值得引入迁移框架）
    _EXPECTED_FILE_COLUMNS: ClassVar[dict[str, str]] = {
        "node_count": "INTEGER NOT NULL DEFAULT 0",
        "error_count": "INTEGER NOT NULL DEFAULT 0",
        "missing_count": "INTEGER NOT NULL DEFAULT 0",
    }

    def initialize(self) -> None:
        with self._lock:
            self._conn.executescript(_DDL)
            self._migrate_files()
            self._conn.execute(
                "INSERT OR IGNORE INTO meta(key, value) VALUES(?, ?)",
                ("schema_version", json.dumps(GRAPH_SCHEMA_VERSION)),
            )
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("schema_version", json.dumps(GRAPH_SCHEMA_VERSION)),
            )
            self._conn.commit()

    def _migrate_files(self) -> None:
        existing = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(files)").fetchall()
        }
        for column, spec in self._EXPECTED_FILE_COLUMNS.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE files ADD COLUMN {column} {spec}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── meta ────────────────────────────────────────────────────────
    def get_meta(self) -> dict[str, Any]:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM meta").fetchall()
        out: dict[str, Any] = {}
        for row in rows:
            try:
                out[row["key"]] = json.loads(row["value"])
            except json.JSONDecodeError:
                out[row["key"]] = row["value"]
        return out

    def set_meta(self, values: dict[str, Any]) -> None:
        with self._lock:
            self._conn.executemany(
                "INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [(k, json.dumps(v, ensure_ascii=False)) for k, v in values.items()],
            )
            self._conn.commit()

    def save_repo_meta(self, meta: RepoMeta) -> None:
        self.set_meta({"repo": meta.to_api(), "repo_id": meta.repo_id})

    def load_repo_meta(self) -> RepoMeta | None:
        raw = self.get_meta().get("repo")
        if not isinstance(raw, dict):
            return None
        return RepoMeta(
            repo_id=raw.get("repoId", ""),
            target=raw.get("target", ""),
            source=raw.get("source", "local"),
            ref=raw.get("ref"),
            root_path=raw.get("rootPath", ""),
            state=raw.get("state", "queued"),
            file_count=int(raw.get("fileCount", 0)),
            loc=int(raw.get("loc", 0)),
            languages=dict(raw.get("languages") or {}),
            indexed_at=raw.get("indexedAt"),
            message=raw.get("message"),
            job_id=raw.get("jobId"),
            commit=raw.get("commit"),
        )

    # ── files ───────────────────────────────────────────────────────
    _FILE_COLUMNS: ClassVar[str] = (
        "path, language, size, loc, sha256, mtime, parse_ok, error, "
        "node_count, error_count, missing_count"
    )

    @staticmethod
    def _row_to_file(row: sqlite3.Row) -> FileRecord:
        return FileRecord(
            path=row["path"],
            language=row["language"],
            size=row["size"],
            loc=row["loc"],
            sha256=row["sha256"],
            mtime=row["mtime"],
            parse_ok=bool(row["parse_ok"]),
            error=row["error"],
            node_count=row["node_count"],
            error_count=row["error_count"],
            missing_count=row["missing_count"],
        )

    def replace_files(self, files: Sequence[FileRecord]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM files")
            self._conn.executemany(
                "INSERT INTO files(path, language, size, loc, sha256, mtime, parse_ok, error, "
                "node_count, error_count, missing_count) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        f.path,
                        f.language,
                        f.size,
                        f.loc,
                        f.sha256,
                        f.mtime,
                        1 if f.parse_ok else 0,
                        f.error,
                        f.node_count,
                        f.error_count,
                        f.missing_count,
                    )
                    for f in files
                ],
            )
            self._conn.commit()

    def update_parse_results(self, outcomes: Sequence[ParseOutcome]) -> int:
        """回填解析统计（node_count / 错误数 / 失败原因）。"""
        if not outcomes:
            return 0
        with self._lock:
            self._conn.executemany(
                "UPDATE files SET node_count = ?, error_count = ?, missing_count = ?, "
                "parse_ok = ?, error = ? WHERE path = ?",
                [
                    (
                        o.node_count,
                        o.error_count,
                        o.missing_count,
                        1 if o.ok else 0,
                        o.error,
                        o.path,
                    )
                    for o in outcomes
                ],
            )
            self._conn.commit()
        return len(outcomes)

    def iter_files(self) -> Iterator[FileRecord]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {self._FILE_COLUMNS} FROM files ORDER BY path"
            ).fetchall()
        for row in rows:
            yield self._row_to_file(row)

    def get_file(self, path: str) -> FileRecord | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {self._FILE_COLUMNS} FROM files WHERE path = ?",
                (path,),
            ).fetchone()
        return self._row_to_file(row) if row is not None else None

    def list_files(
        self, *, query: str = "", limit: int = 300, offset: int = 0
    ) -> tuple[list[FileRecord], int]:
        """按路径子串过滤的分页列表（CST 视图的文件选择器 / M3 搜索的基础）。"""
        pattern = f"%{query}%" if query else "%"
        with self._lock:
            total = int(
                self._conn.execute(
                    "SELECT COUNT(*) AS n FROM files WHERE path LIKE ?", (pattern,)
                ).fetchone()["n"]
            )
            rows = self._conn.execute(
                f"SELECT {self._FILE_COLUMNS} FROM files WHERE path LIKE ? "
                "ORDER BY path LIMIT ? OFFSET ?",
                (pattern, max(1, limit), max(0, offset)),
            ).fetchall()
        return ([self._row_to_file(row) for row in rows], total)

    def total_nodes(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(node_count), 0) AS n FROM files"
            ).fetchone()
        return int(row["n"])

    def parse_error_files(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM files WHERE error_count > 0 OR missing_count > 0"
            ).fetchone()
        return int(row["n"])

    def failed_parse_files(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM files WHERE parse_ok = 0"
            ).fetchone()
        return int(row["n"])

    def language_stats(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT language, COUNT(*) AS n FROM files GROUP BY language ORDER BY n DESC"
            ).fetchall()
        return {row["language"]: int(row["n"]) for row in rows}

    def file_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM files").fetchone()
        return int(row["n"])

    def total_loc(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(SUM(loc), 0) AS n FROM files").fetchone()
        return int(row["n"])

    # ── 图谱：写入 ──────────────────────────────────────────────────
    _NODE_COLUMNS: ClassVar[str] = (
        "id, kind, name, qualified, file, sl, sc, el, ec, language, community, extra"
    )
    _EDGE_COLUMNS: ClassVar[str] = "src, dst, relation, confidence, file, sl, sc, el, ec"

    def replace_graph(self, nodes: Sequence[NodeRec], edges: Sequence[EdgeRec]) -> None:
        """整体替换图谱（M3 全量重建；增量更新留到后续里程碑）。"""
        with self._lock:
            self._conn.execute("DELETE FROM nodes")
            self._conn.execute("DELETE FROM edges")
            self._conn.executemany(
                "INSERT INTO nodes(id, kind, name, qualified, file, sl, sc, el, ec, language, "
                "community, extra) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    (
                        n.id,
                        n.kind,
                        n.name,
                        n.qualified,
                        n.file,
                        n.sl,
                        n.sc,
                        n.el,
                        n.ec,
                        n.language,
                        n.community,
                        json.dumps(n.extra, ensure_ascii=False) if n.extra else None,
                    )
                    for n in nodes
                ],
            )
            self._conn.executemany(
                "INSERT OR IGNORE INTO edges(src, dst, relation, confidence, file, sl, sc, el, ec) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                [
                    (
                        e.src,
                        e.dst,
                        e.relation,
                        e.confidence,
                        e.file,
                        e.sl,
                        e.sc,
                        e.el,
                        e.ec,
                    )
                    for e in edges
                ],
            )
            self._conn.commit()

    def replace_communities(self, communities: Sequence[CommunityInfo]) -> None:
        """重写社区表；已缓存过命名的社区（按内容指纹）直接把名字带回来。"""
        with self._lock:
            # 注意：sqlite3.Row 不支持 .get()，先转成普通 dict
            cached: dict[str, dict[str, Any]] = {
                row["content_hash"]: {
                    "name": row["name"],
                    "summary": row["summary"],
                    "named_by": row["named_by"],
                }
                for row in self._conn.execute(
                    "SELECT content_hash, name, summary, named_by FROM naming_cache"
                ).fetchall()
            }
            self._conn.execute("DELETE FROM communities")
            self._conn.executemany(
                "INSERT INTO communities(id, name, summary, size, cohesion, named_by, content_hash) "
                "VALUES(?,?,?,?,?,?,?)",
                [
                    (
                        c.id,
                        (cached.get(c.content_hash) or {}).get("name") or c.name,
                        (cached.get(c.content_hash) or {}).get("summary"),
                        c.size,
                        c.cohesion,
                        (cached.get(c.content_hash) or {}).get("named_by") or c.named_by,
                        c.content_hash,
                    )
                    for c in communities
                ],
            )
            self._conn.commit()

    def update_community_names(self, entries: Sequence[dict[str, Any]]) -> None:
        """更新社区名称/摘要，并写入按内容指纹的命名缓存（重索引后可复用）。"""
        if not entries:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE communities SET name = ?, summary = ?, named_by = ? WHERE id = ?",
                [
                    (entry["name"], entry.get("summary"), entry.get("named_by", "llm"), entry["id"])
                    for entry in entries
                ],
            )
            self._conn.executemany(
                "INSERT INTO naming_cache(content_hash, name, summary, named_by, updated_at) "
                "SELECT content_hash, ?, ?, ?, ? FROM communities WHERE id = ? "
                "ON CONFLICT(content_hash) DO UPDATE SET name=excluded.name, "
                "summary=excluded.summary, named_by=excluded.named_by, updated_at=excluded.updated_at",
                [
                    (
                        entry["name"],
                        entry.get("summary"),
                        entry.get("named_by", "llm"),
                        now_iso_stub(),
                        entry["id"],
                    )
                    for entry in entries
                ],
            )
            self._conn.commit()

    # ── 抽取结果缓存（增量索引用）──────────────────────────────────
    def save_extractions(self, rows: Sequence[tuple[str, str, str]]) -> None:
        """``rows`` 为 ``(path, sha256, payload_json)``。"""
        if not rows:
            return
        with self._lock:
            self._conn.executemany(
                "INSERT INTO extractions(path, sha256, payload, updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET sha256=excluded.sha256, "
                "payload=excluded.payload, updated_at=excluded.updated_at",
                [(path, sha, payload, now_iso_stub()) for path, sha, payload in rows],
            )
            self._conn.commit()

    def load_extractions(self) -> dict[str, tuple[str, str]]:
        """返回 ``{path: (sha256, payload_json)}``。"""
        with self._lock:
            rows = self._conn.execute("SELECT path, sha256, payload FROM extractions").fetchall()
        return {row["path"]: (row["sha256"], row["payload"]) for row in rows}

    def drop_extractions(self, paths: Sequence[str]) -> None:
        if not paths:
            return
        with self._lock:
            self._conn.executemany(
                "DELETE FROM extractions WHERE path = ?", [(path,) for path in paths]
            )
            self._conn.commit()

    def naming_cache(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT content_hash, name, summary, named_by FROM naming_cache"
            ).fetchall()
        return {
            row["content_hash"]: {
                "name": row["name"],
                "summary": row["summary"],
                "namedBy": row["named_by"],
            }
            for row in rows
        }

    def set_node_communities(self, mapping: dict[str, int]) -> None:
        if not mapping:
            return
        with self._lock:
            self._conn.executemany(
                "UPDATE nodes SET community = ? WHERE id = ?",
                [(community, node_id) for node_id, community in mapping.items()],
            )
            self._conn.commit()

    def rebuild_search_index(self) -> None:
        """把符号写进 FTS5（名称 / 限定名 / 文件 / doc）。"""
        with self._lock:
            self._conn.execute("DELETE FROM symbol_fts")
            self._conn.execute(
                "INSERT INTO symbol_fts(name, qualified, file, doc, snippet) "
                "SELECT name, qualified, COALESCE(file, ''), "
                "COALESCE(json_extract(extra, '$.doc'), ''), '' FROM nodes "
                "WHERE kind NOT IN ('repo', 'dir', 'file', 'external', 'community')"
            )
            self._conn.commit()

    # ── 图谱：读取 ──────────────────────────────────────────────────
    @staticmethod
    def _row_to_node(row: sqlite3.Row) -> dict[str, Any]:
        extra: dict[str, Any] = {}
        if row["extra"]:
            try:
                extra = json.loads(row["extra"])
            except json.JSONDecodeError:
                extra = {}
        return {
            "id": row["id"],
            "kind": row["kind"],
            "name": row["name"],
            "qualified": row["qualified"],
            "file": row["file"],
            "language": row["language"],
            "community": row["community"],
            "start": [row["sl"], row["sc"]],
            "end": [row["el"], row["ec"]],
            "degree": int(extra.get("degree", 0)),
            "inDegree": int(extra.get("inDegree", 0)),
            "outDegree": int(extra.get("outDegree", 0)),
            "doc": extra.get("doc"),
            "external": bool(extra.get("external", False)),
        }

    @staticmethod
    def _row_to_edge(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "source": row["src"],
            "target": row["dst"],
            "relation": row["relation"],
            "confidence": row["confidence"],
            "file": row["file"],
            "start": [row["sl"], row["sc"]],
        }

    def graph_counts(self) -> dict[str, int]:
        with self._lock:
            nodes = int(self._conn.execute("SELECT COUNT(*) AS n FROM nodes").fetchone()["n"])
            edges = int(self._conn.execute("SELECT COUNT(*) AS n FROM edges").fetchone()["n"])
            files = int(
                self._conn.execute("SELECT COUNT(*) AS n FROM nodes WHERE kind='file'").fetchone()[
                    "n"
                ]
            )
        return {"nodes": nodes, "edges": edges, "fileNodes": files}

    def group_counts(self) -> dict[str, dict[str, int]]:
        with self._lock:
            by_kind = {
                row["kind"]: int(row["n"])
                for row in self._conn.execute(
                    "SELECT kind, COUNT(*) AS n FROM nodes GROUP BY kind ORDER BY n DESC"
                ).fetchall()
            }
            by_relation = {
                row["relation"]: int(row["n"])
                for row in self._conn.execute(
                    "SELECT relation, COUNT(*) AS n FROM edges GROUP BY relation ORDER BY n DESC"
                ).fetchall()
            }
            by_confidence = {
                row["confidence"]: int(row["n"])
                for row in self._conn.execute(
                    "SELECT confidence, COUNT(*) AS n FROM edges "
                    "GROUP BY confidence ORDER BY n DESC"
                ).fetchall()
            }
        return {"byKind": by_kind, "byRelation": by_relation, "byConfidence": by_confidence}

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                f"SELECT {self._NODE_COLUMNS} FROM nodes WHERE id = ?", (node_id,)
            ).fetchone()
        return self._row_to_node(row) if row else None

    def iter_nodes(
        self,
        *,
        kinds: Sequence[str] | None = None,
        communities: Sequence[int] | None = None,
        limit: int | None = None,
        order_by_degree: bool = True,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if kinds:
            clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        if communities:
            clauses.append(f"community IN ({','.join('?' * len(communities))})")
            params.extend(communities)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = (
            "ORDER BY COALESCE(json_extract(extra, '$.degree'), 0) DESC, id"
            if order_by_degree
            else "ORDER BY id"
        )
        sql = f"SELECT {self._NODE_COLUMNS} FROM nodes {where} {order}"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_node(row) for row in rows]

    def iter_edges(
        self,
        *,
        relations: Sequence[str] | None = None,
        confidences: Sequence[str] | None = None,
        node_ids: Sequence[str] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if relations:
            clauses.append(f"relation IN ({','.join('?' * len(relations))})")
            params.extend(relations)
        if confidences:
            clauses.append(f"confidence IN ({','.join('?' * len(confidences))})")
            params.extend(confidences)
        if node_ids:
            placeholders = ",".join("?" * len(node_ids))
            clauses.append(f"src IN ({placeholders}) AND dst IN ({placeholders})")
            params.extend(node_ids)
            params.extend(node_ids)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT {self._EDGE_COLUMNS} FROM edges {where}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_edge(row) for row in rows]

    def edges_for_node(self, node_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {self._EDGE_COLUMNS} FROM edges WHERE src = ? OR dst = ? LIMIT ?",
                (node_id, node_id, limit),
            ).fetchall()
        return [self._row_to_edge(row) for row in rows]

    def nodes_by_ids(self, node_ids: Sequence[str]) -> list[dict[str, Any]]:
        if not node_ids:
            return []
        placeholders = ",".join("?" * len(node_ids))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {self._NODE_COLUMNS} FROM nodes WHERE id IN ({placeholders})",
                list(node_ids),
            ).fetchall()
        return [self._row_to_node(row) for row in rows]

    def search_nodes(
        self, query: str, *, kind: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        text = (query or "").strip()
        if not text:
            return []
        pattern = f"%{text}%"
        clauses = [
            "(name LIKE ? OR qualified LIKE ? OR file LIKE ?)",
            "kind NOT IN ('repo', 'dir', 'community')",
        ]
        params: list[Any] = [pattern, pattern, pattern]
        if kind:
            clauses.append("kind = ?")
            params.append(kind)
        sql = (
            f"SELECT {self._NODE_COLUMNS} FROM nodes WHERE {' AND '.join(clauses)} "
            "ORDER BY COALESCE(json_extract(extra, '$.degree'), 0) DESC, qualified LIMIT ?"
        )
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_node(row) for row in rows]

    def symbol_counts_by_file(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT file, COUNT(*) AS n FROM nodes "
                "WHERE file IS NOT NULL AND kind NOT IN ('repo', 'dir', 'file', 'external', "
                "'community') GROUP BY file"
            ).fetchall()
        return {row["file"]: int(row["n"]) for row in rows}

    def community_rows(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, summary, size, cohesion, named_by, content_hash FROM communities "
                "ORDER BY size DESC"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "name": row["name"],
                "summary": row["summary"],
                "size": row["size"],
                "cohesion": row["cohesion"],
                "namedBy": row["named_by"],
                "contentHash": row["content_hash"],
            }
            for row in rows
        ]


# ── 目录级辅助 ──────────────────────────────────────────────────────


def now_iso_stub() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path_for(settings: Settings, repo_id: str) -> Path:
    return settings.db_dir / f"{repo_id}.sqlite"


def open_store(settings: Settings, repo_id: str) -> Store:
    return Store(db_path_for(settings, repo_id))


def list_repo_metas(settings: Settings) -> list[RepoMeta]:
    """枚举所有已建库的仓库（按索引时间倒序）。"""
    settings.ensure_dirs()
    metas: list[RepoMeta] = []
    for db_file in sorted(settings.db_dir.glob("*.sqlite")):
        try:
            with Store(db_file) as store:
                meta = store.load_repo_meta()
        except sqlite3.DatabaseError:
            continue
        if meta is not None:
            metas.append(meta)
    metas.sort(key=lambda m: m.indexed_at or "", reverse=True)
    return metas


def delete_repo_data(settings: Settings, repo_id: str) -> bool:
    """删除仓库的数据库与 WAL 附属文件；克隆目录由调用方按 root_path 处理。"""
    db_file = db_path_for(settings, repo_id)
    if not db_file.exists():
        return False
    for path in (db_file, Path(f"{db_file}-wal"), Path(f"{db_file}-shm")):
        path.unlink(missing_ok=True)
    return True
