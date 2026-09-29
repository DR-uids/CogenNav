#!/usr/bin/env python
"""CST 性能基准：索引一个仓库并测量解析/CST 视图的实际开销。

用法：
    PYTHONPATH=src .venv/bin/python scripts/bench.py            # 默认 psf/requests
    PYTHONPATH=src .venv/bin/python scripts/bench.py <target>

指标（对照计划 §7 M2 验收：万节点文件首次响应 < 300ms 且不整树传输）：
- 各阶段耗时与解析统计（进程池模式 / 是否降级）
- 最大文件的 ``/cst`` 根请求耗时与 JSON 体积（depth=4）
- 懒加载单个子树的耗时与体积
"""

from __future__ import annotations

import gzip
import json
import time
from pathlib import Path

from cogen.config import get_settings
from cogen.graph.store import db_path_for, open_store
from cogen.ingest.pipeline import index_repo
from cogen.ingest.resolve import clone_path, index_root, repo_id, resolve_target
from cogen.jobs import Progress
from cogen.parse.cst import CstOptions, serialize_subtree
from cogen.parse.parser import parse_file_cached


class _SilentProgress(Progress):
    """不写库、不广播的进度接收器（基准用）。"""

    def __init__(self) -> None:
        self.phase = "resolve"
        self.state = "running"
        self.message = None

    def update(self, **kwargs: object) -> None:  # type: ignore[override]
        if kwargs.get("phase"):
            self.phase = str(kwargs["phase"])
        if kwargs.get("message"):
            self.message = str(kwargs["message"])


def main(target: str) -> int:
    settings = get_settings()
    settings.ensure_dirs()
    spec = resolve_target(target)
    rid = repo_id(spec)
    print(f"目标: {target}  repoId={rid}")
    print(f"进程池: workers={settings.effective_parse_workers()} chunk_timeout={settings.parse_chunk_timeout_s}s")

    started = time.perf_counter()
    with open_store(settings, rid) as store:
        index_repo(spec, settings, store, _SilentProgress())  # type: ignore[arg-type]
        meta = store.load_repo_meta()
        extra = store.get_meta()
        print(f"\n索引耗时: {time.perf_counter() - started:.2f}s")
        print(f"文件: {meta.file_count if meta else 0}  行数: {meta.loc if meta else 0}")
        print(f"解析统计: {json.dumps(extra.get('parse'), ensure_ascii=False)}")
        print(f"语言分布: {json.dumps(meta.languages if meta else {}, ensure_ascii=False)}")

        files = sorted(store.iter_files(), key=lambda f: f.node_count, reverse=True)[:3]
        root = Path(meta.root_path) if meta else index_root(settings, spec)

    print("\n最大文件（按 CST 节点数）:")
    for record in files:
        print(f"  {record.path}  nodes={record.node_count} loc={record.loc} lang={record.language}")

    if not files:
        return 1

    biggest = files[0]
    absolute = root / biggest.path
    options = CstOptions(depth=4).clamped()

    # 首次（冷）与后续（热）请求
    for label in ("冷启动", "热缓存"):
        t0 = time.perf_counter()
        parsed = parse_file_cached(absolute, biggest.language)
        payload = serialize_subtree(parsed.source, parsed.root, depth=options.depth, options=options)
        elapsed = (time.perf_counter() - t0) * 1000
        raw = json.dumps(payload, ensure_ascii=False).encode()
        print(
            f"\n{label} /cst 根请求: {elapsed:.1f} ms  JSON={len(raw) / 1024:.1f} KiB  "
            f"gzip={len(gzip.compress(raw)) / 1024:.1f} KiB  totalNodes={biggest.node_count}"
        )

    # 懒加载一个子树
    child = payload["children"][0] if payload["children"] else None
    if child is not None:
        t0 = time.perf_counter()
        sub = serialize_subtree(
            parsed.source, parsed.root.children[0], depth=options.depth, options=options
        )
        elapsed = (time.perf_counter() - t0) * 1000
        print(
            f"懒加载子节点 0: {elapsed:.1f} ms  JSON={len(json.dumps(sub, ensure_ascii=False)) / 1024:.1f} KiB"
        )

    print(f"\n快照目录: {clone_path(settings, spec)}")
    print(f"数据库: {db_path_for(settings, rid)}")
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "psf/requests"))
