"""文件与 CST 接口：文件列表、源码文本、惰性 CST 子树。

安全约定（计划 §9）：
- 路径先过 ``check_repo_relative_path``（拒绝对路径 / ``..`` / 控制字符）；
- 解析成绝对路径后再过 ``ensure_within``（防符号链接穿越）；
- **只有已索引的文件才能读**（`store.get_file` 命中），因此被跳过的 ``.env`` 等读不到。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from ..config import Settings, get_settings
from ..graph.schema import FileRecord
from ..graph.store import Store, db_path_for
from ..i18n import t
from ..parse.cst import CstOptions, CstPathError, field_of, resolve_node, serialize_subtree
from ..parse.parser import (
    UnknownLanguageError,
    parse_file_cached,
    parse_source,
    resolve_language,
)
from ..security import PathEscapeError, UnsafePathError, check_repo_relative_path, ensure_within
from .deps import check_repo_id, get_manager

router = APIRouter(prefix="/api/repos/{repo_id}", tags=["files"])


def file_to_api(record: FileRecord) -> dict[str, object]:
    return {
        "path": record.path,
        "language": record.language,
        "size": record.size,
        "loc": record.loc,
        "nodeCount": record.node_count,
        "errorCount": record.error_count,
        "parseOk": record.parse_ok,
        "error": record.error,
    }


@contextmanager
def repo_context(repo_id: str) -> Iterator[tuple[Store, Path, Settings]]:
    """打开仓库库并解析索引根目录，退出时关闭连接。"""
    rid = check_repo_id(repo_id)
    settings = get_settings()
    db_file = db_path_for(settings, rid)
    if not db_file.exists():
        raise HTTPException(status_code=404, detail=t("api.repoNotFound"))
    store = Store(db_file)
    try:
        meta = store.load_repo_meta()
        if meta is None:
            raise HTTPException(status_code=404, detail=t("api.repoNotFound"))
        root = Path(meta.root_path)
        if not root.is_dir():
            # 「快照还没建出来」（克隆/遍历还在跑）与「快照被清理掉了」是两回事：
            # 前者是暂时的，前端只要等任务终态再读即可；后者才需要用户重新索引。
            if get_manager().active_for_repo(rid) is not None:
                raise HTTPException(status_code=409, detail=t("api.indexInProgress"))
            raise HTTPException(status_code=410, detail=t("api.snapshotGone"))
        yield store, root, settings
    finally:
        store.close()


def _safe_rel(raw: str) -> str:
    try:
        return check_repo_relative_path(raw)
    except UnsafePathError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _absolute(root: Path, rel: str) -> Path:
    try:
        candidate = ensure_within(root, root / rel)
    except PathEscapeError as exc:
        raise HTTPException(status_code=404, detail=t("api.fileNotFound")) from exc
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail=t("api.fileNotFound"))
    return candidate


def _read_source(path: Path, settings: Settings) -> bytes:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HTTPException(
            status_code=500, detail=t("api.readFailed", error=exc.strerror or exc)
        ) from exc
    if len(data) > settings.max_file_bytes:
        raise HTTPException(status_code=413, detail=t("api.fileTooLarge"))
    return data


def _indexed_file(store: Store, rel: str) -> FileRecord:
    record = store.get_file(rel)
    if record is None:
        raise HTTPException(status_code=404, detail=t("api.fileNotIndexed"))
    return record


@router.get("/files")
def list_files(
    repo_id: str,
    q: str = Query("", max_length=512),
    limit: int = Query(300, ge=1, le=2000),
    offset: int = Query(0, ge=0),
) -> dict[str, object]:
    """按路径子串过滤的文件列表（CST 视图的文件选择器）。"""
    with repo_context(repo_id) as (store, _root, _settings):
        records, total = store.list_files(query=q.strip(), limit=limit, offset=offset)
        return {"total": total, "files": [file_to_api(r) for r in records]}


@router.get("/file")
def get_file_text(
    repo_id: str,
    path: str = Query(..., min_length=1, max_length=1024),
) -> dict[str, object]:
    """返回源码文本（供右侧代码面板渲染）。"""
    with repo_context(repo_id) as (store, root, settings):
        rel = _safe_rel(path)
        record = _indexed_file(store, rel)
        data = _read_source(_absolute(root, rel), settings)
        return {
            "path": rel,
            "language": record.language,
            "size": record.size,
            "lines": record.loc,
            "text": data.decode("utf-8", "replace"),
            "truncated": False,
            "nodeCount": record.node_count,
            "errorCount": record.error_count,
            # 磁盘内容与索引时不一致（例如索引后改了文件）→ 前端可提示重新索引
            "stale": len(data) != record.size,
        }


@router.get("/cst")
def get_cst(
    repo_id: str,
    path: str = Query(..., min_length=1, max_length=1024),
    node_path: str = Query("", alias="nodePath", max_length=200),
    depth: int = Query(4, ge=1, le=12),
    fmt: str = Query("tree", alias="format", pattern="^(tree|sexp)$"),
) -> dict[str, object]:
    """按 ``nodePath`` + ``depth`` 返回惰性子树（默认 depth=4，绝不整树传输）。"""
    with repo_context(repo_id) as (store, root, settings):
        rel = _safe_rel(path)
        record = _indexed_file(store, rel)
        language = record.language
        if resolve_language(language) is None:
            raise HTTPException(
                status_code=415,
                detail=t("api.noGrammarForLanguage", language=language),
            )
        absolute = _absolute(root, rel)

        options = CstOptions(depth=depth).clamped()
        try:
            if fmt == "sexp":
                parsed = parse_file_cached(absolute, language)
                return {
                    "path": rel,
                    "language": language,
                    "format": "sexp",
                    "totalNodes": record.node_count or parsed.node_count,
                    "sexp": str(parsed.root)[:20000],
                }
            data = _read_source(absolute, settings)
            parsed = parse_source(data, language)
            chain = resolve_node(parsed.root, node_path)
        except CstPathError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except UnknownLanguageError as exc:
            raise HTTPException(status_code=415, detail=str(exc)) from exc

        payload = serialize_subtree(
            parsed.index,
            chain[-1],
            field=field_of(chain),
            depth=options.depth,
            options=options,
        )
        return {
            "path": rel,
            "language": language,
            "nodePath": node_path,
            "depth": options.depth,
            "totalNodes": record.node_count or parsed.node_count,
            "stale": len(data) != record.size,
            "node": payload,
        }
