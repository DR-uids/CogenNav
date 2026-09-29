"""索引流水线：M1 覆盖 resolve → clone → walk。

后续里程碑（M2 parse / M3 extract+build+analyze / M4 name）在这里追加阶段，
每个阶段只通过 SQLite 表与 plain dict 传递数据。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import PurePosixPath
from typing import Any

from ..config import Settings
from ..extract.base import FileExtraction, extraction_from_dict, extraction_to_dict
from ..extract.resolve_calls import ReferenceResolver
from ..graph.analyze import analyze_graph
from ..graph.build import build_graph
from ..graph.schema import FileRecord, RepoMeta
from ..graph.store import Store
from ..i18n import t
from ..jobs import Progress, now_iso
from ..parse.parser import is_parsable
from ..parse.pool import FileAnalysis, ParsePool, analyze_task
from ..security import redact_secrets
from .clone import clone_repo
from .resolve import Target, clone_path, index_root, repo_id
from .walk import walk_repo

#: 报告里最多列出的跳过原因
_SKIP_TOP_N = 5


def _file_sha(files: list[FileRecord], path: str) -> str:
    for record in files:
        if record.path == path:
            return record.sha256
    return ""


def index_repo(target: Target, settings: Settings, store: Store, progress: Progress) -> None:
    """执行一次完整索引。异常会同时落到任务状态与仓库 meta 上。"""
    meta = store.load_repo_meta()
    if meta is None:
        meta = RepoMeta(
            repo_id=repo_id(target),
            target=target.raw,
            source=target.kind,
            ref=target.ref,
            root_path=str(index_root(settings, target)),
        )

    try:
        meta.state = "running"
        meta.message = t("pipeline.metaIndexing")
        store.save_repo_meta(meta)
        progress.say("pipeline.targetResolved", phase="resolve", force=True)

        commit = None
        if target.kind == "git":
            progress.say("pipeline.cloning", phase="clone", force=True)
            commit = clone_repo(
                target,
                clone_path(settings, target),
                settings,
                on_message=lambda msg: progress.update(phase="clone", message=msg, force=True),
            )
        meta.commit = commit

        root = index_root(settings, target)
        if not root.is_dir():
            raise FileNotFoundError(t("pipeline.rootMissing", root=root))
        # 快照路径可能因为 COGEN_HOME / 工作区被搬动而变过：每次索引都以本次算出的
        # root 为准回写 meta，避免库里留着旧绝对路径导致读接口永久 410。
        meta.root_path = str(root)

        progress.say("pipeline.walking", params={"root": root}, phase="walk", force=True)

        def on_progress(count: int, file: str) -> None:
            progress.say(
                "pipeline.foundFiles",
                params={"count": count},
                phase="walk",
                current=count,
                file=file or None,
            )

        result = walk_repo(root, settings, on_progress=on_progress)
        store.replace_files(result.files)
        progress.say(
            "pipeline.foundFiles",
            params={"count": len(result.files)},
            phase="walk",
            current=len(result.files),
            total=len(result.files),
            file=None,
            force=True,
        )

        # ── 增量：sha256 未变的文件直接复用上次的解析统计与抽取结果 ─────
        previous_files = {record.path: record for record in store.iter_files()}
        cached_extractions = store.load_extractions()
        reused_extractions: dict[str, FileExtraction] = {}
        changed: list[FileRecord] = []
        for record in result.files:
            previous = previous_files.get(record.path)
            cached = cached_extractions.get(record.path)
            if (
                previous is not None
                and cached is not None
                and previous.sha256 == record.sha256
                and cached[0] == record.sha256
            ):
                # 解析统计沿用旧值（replace_files 会把整表重写）
                record.node_count = previous.node_count
                record.error_count = previous.error_count
                record.missing_count = previous.missing_count
                record.parse_ok = previous.parse_ok
                record.error = previous.error
                reused_extractions[record.path] = extraction_from_dict(json.loads(cached[1]))
            else:
                changed.append(record)

        if previous_files:
            progress.say(
                "pipeline.reused",
                params={"reused": len(reused_extractions), "changed": len(changed)},
                phase="parse",
                force=True,
            )

        # ── parse + extract 阶段：分进程解析并抽取符号（只跑变更文件）────
        parsable = [(f.path, f.language) for f in changed if is_parsable(f.language)]
        parse_stats: dict[str, Any] = {"parsed": 0, "failed": 0, "errors": 0, "nodes": 0}
        analyses: list[FileAnalysis] = []
        if parsable:
            progress.say(
                "pipeline.parsing",
                params={"count": len(parsable)},
                phase="parse",
                current=0,
                total=len(parsable),
                file=None,
                force=True,
            )

            def on_parse(done: int, total: int, current_file: str) -> None:
                progress.say(
                    "pipeline.parsed",
                    params={"done": done, "total": total},
                    phase="parse",
                    current=done,
                    total=total,
                    file=current_file,
                )

            with ParsePool(settings, task=analyze_task) as pool:
                analyses = pool.parse_files(root, parsable, progress=on_parse)
                parse_mode = pool.mode
                degraded = pool.degraded_reason
            outcomes = [item.outcome for item in analyses]
            store.update_parse_results(outcomes)
            parse_stats = {
                "parsed": sum(1 for o in outcomes if o.ok),
                "failed": sum(1 for o in outcomes if not o.ok),
                "errors": sum(1 for o in outcomes if o.ok and (o.error_count or o.missing_count)),
                "nodes": sum(o.node_count for o in outcomes),
                "timeouts": sum(1 for o in outcomes if o.error == "parse_timeout"),
                "mode": parse_mode,
                "degraded": degraded,
            }

        # ── build + analyze 阶段：建图、社区发现、依赖环 ────────────────
        extractions: dict[str, FileExtraction] = dict(reused_extractions)
        for item in analyses:
            if item.extraction is not None:
                extractions[item.outcome.path] = item.extraction
        # 抽取结果落库，供下次增量索引复用
        store.save_extractions(
            [
                (path, _file_sha(result.files, path), json.dumps(extraction_to_dict(extraction)))
                for path, extraction in extractions.items()
            ]
        )
        progress.say(
            "pipeline.building",
            params={"count": len(extractions)},
            phase="build",
            force=True,
        )
        resolution = ReferenceResolver(extractions).resolve()
        graph = build_graph(
            repo_id=meta.repo_id,
            repo_name=PurePosixPath(meta.root_path).name or meta.repo_id,
            files=result.files,
            extractions=extractions,
            resolution=resolution,
            commit=commit,
        )
        store.replace_graph(graph.nodes, graph.edges)

        progress.say("pipeline.analyzing", phase="analyze", force=True)
        analysis = analyze_graph(
            graph.nodes, graph.edges, resolved_call_rate=resolution.stats.resolved_rate
        )
        store.replace_communities(analysis.communities)
        store.set_node_communities(analysis.node_community)
        store.rebuild_search_index()
        graph_stats = graph.stats

        stale_extractions = [
            path for path in cached_extractions if path not in {f.path for f in result.files}
        ]
        store.drop_extractions(stale_extractions)

        meta.file_count = len(result.files)
        meta.loc = sum(f.loc for f in result.files)
        meta.languages = store.language_stats()
        meta.indexed_at = now_iso()
        meta.state = "done"

        summary = Counter(s.reason for s in result.skipped)
        message = t("pipeline.summary.base", files=meta.file_count, loc=meta.loc)
        if reused_extractions and len(changed) < len(result.files):
            message += t("pipeline.summary.incremental", reused=len(reused_extractions))
        if parsable:
            message += t(
                "pipeline.summary.parsed",
                parsed=parse_stats["parsed"],
                nodes=parse_stats["nodes"],
            )
            if parse_stats["errors"]:
                message += t("pipeline.summary.syntaxErrors", count=parse_stats["errors"])
            if parse_stats["failed"]:
                message += t("pipeline.summary.parseFailed", count=parse_stats["failed"])
            if parse_stats.get("mode") == "serial":
                message += t("pipeline.summary.serial")
        message += t(
            "pipeline.summary.graph",
            nodes=graph_stats["nodes"],
            edges=graph_stats["edges"],
            communities=len(analysis.communities),
        )
        rate = graph_stats.get("resolvedCallRate")
        if isinstance(rate, float):
            message += t("pipeline.summary.resolvedRate", rate=f"{rate:.0%}")
        if analysis.cycles:
            message += t("pipeline.summary.cycles", count=len(analysis.cycles))
        if result.truncated:
            message += t("pipeline.summary.truncated", reason=result.truncated_reason)
        if summary:
            separator = t("pipeline.summary.separator")
            top = separator.join(f"{reason} {n}" for reason, n in summary.most_common(_SKIP_TOP_N))
            message += t("pipeline.summary.skipped", count=len(result.skipped), top=top)
        meta.message = message
        store.save_repo_meta(meta)
        store.set_meta(
            {
                "skipped": dict(summary),
                "truncated": result.truncated,
                "truncated_reason": result.truncated_reason,
                "commit": commit,
                "parse": parse_stats,
                "graph": graph_stats,
                "analysis": {
                    **analysis.stats,
                    "godNodes": analysis.god_nodes,
                    "cycles": analysis.cycles,
                    "orphans": analysis.orphans,
                    "crossCommunity": analysis.cross_community,
                },
            }
        )
        progress.done(message)
    except Exception as exc:
        meta.state = "error"
        meta.message = redact_secrets(f"{type(exc).__name__}: {exc}")
        store.save_repo_meta(meta)
        raise
