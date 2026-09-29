"""导出 GRAPH_REPORT.md：给人和 LLM 看的架构报告。

与 ``graphify`` 的 GRAPH_REPORT.md 定位一致：关键概念、值得注意的耦合、待办热点、
可疑死代码，全部带"怎么复现"的说明，避免结论无法核对。
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from ..config import Settings
from ..graph import tools
from ..graph.store import Store

MAX_SECTION_ITEMS = 15
MAX_MARKER_FILES = 10

_MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK|NOTE)\b[:\s]?(.{0,80})")
_SYMBOL_KINDS = ("function", "method")
_MARKER_SCAN_BYTES = 200_000


def _scan_markers(root_path: str, files: list[str]) -> tuple[Counter[str], dict[str, list[str]]]:
    """统计 TODO/FIXME 等标记（按文件计数，保留少量样例）。"""
    counter: Counter[str] = Counter()
    samples: dict[str, list[str]] = {}
    for rel in files:
        path = Path(root_path) / rel
        try:
            if path.stat().st_size > _MARKER_SCAN_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        found = _MARKER_RE.findall(text)
        if not found:
            continue
        counter[rel] = len(found)
        samples[rel] = [f"{kind}: {detail.strip()}" for kind, detail in found[:2]]
    return counter, samples


def generate_report(store: Store, settings: Settings, *, output: Path | None = None) -> Path:
    meta = store.load_repo_meta()
    stored = store.get_meta()
    graph_stats = stored.get("graph") or {}
    parse_stats = stored.get("parse") or {}
    analysis = tools.analysis_payload(store)
    identifier = meta.repo_id if meta else "repo"
    target = output or (settings.exports_dir / identifier / "GRAPH_REPORT.md")
    target.parent.mkdir(parents=True, exist_ok=True)

    lines: list[str] = []
    add = lines.append
    add(f"# {meta.target if meta else identifier} 架构报告")
    add("")
    add(
        f"> 由 cogen {__version__} 生成于 "
        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    )
    add("")

    # ── 概览 ────────────────────────────────────────────────────────
    add("## 概览")
    add("")
    if meta:
        add(f"- 仓库：`{meta.target}`（{meta.source}，ref={meta.ref or 'HEAD'}）")
        add(f"- 规模：{meta.file_count} 个文件 / {meta.loc} 行")
        languages = "、".join(f"{name} {count}" for name, count in list(meta.languages.items())[:8])
        add(f"- 语言分布：{languages}")
        if meta.commit:
            add(f"- commit：`{meta.commit}`")
    add(
        f"- 图谱：{graph_stats.get('nodes', 0)} 节点 / {graph_stats.get('edges', 0)} 边"
        f"（社区 {len(analysis['communities'])} 个）"
    )
    if parse_stats:
        add(
            f"- 解析：{parse_stats.get('parsed', 0)} 个文件成功"
            f"（{parse_stats.get('nodes', 0)} 个 CST 节点），"
            f"{parse_stats.get('errors', 0)} 个含语法错误，{parse_stats.get('failed', 0)} 个失败"
        )
    rate = graph_stats.get("resolvedCallRate")
    if isinstance(rate, float):
        add(
            f"- 调用解析率：**{rate:.0%}**"
            f"（EXTRACTED {graph_stats.get('callsExtracted', 0)} / "
            f"INFERRED {graph_stats.get('callsInferred', 0)} / "
            f"AMBIGUOUS {graph_stats.get('callsAmbiguous', 0)} / "
            f"未解析 {graph_stats.get('callsUnresolved', 0)}）"
        )
    add("")

    # ── 社区 ────────────────────────────────────────────────────────
    add("## 主要社区（模块聚类）")
    add("")
    add("| # | 名称 | 符号数 | 内聚度 | 代表符号 |")
    add("|---|------|-------|--------|----------|")
    for community in analysis["communities"][:MAX_SECTION_ITEMS]:
        top = "、".join(symbol["name"] for symbol in community.get("topSymbols", [])[:3])
        cohesion = community.get("cohesion")
        add(
            f"| {community['id']} | {community['name']} | {community['size']} | "
            f"{cohesion if cohesion is not None else '-'} | {top} |"
        )
    add("")

    # ── 关键节点 ────────────────────────────────────────────────────
    add("## 关键节点（god nodes）")
    add("")
    for node in analysis["godNodes"][:MAX_SECTION_ITEMS]:
        add(
            f"- `{node['name']}`（{node['kind']}，度数 {node['degree']}，"
            f"入 {node['inDegree']} / 出 {node['outDegree']}）— `{node['file']}`"
        )
    add("")

    # ── 依赖环 ──────────────────────────────────────────────────────
    add("## 依赖环（import cycles）")
    add("")
    if analysis["cycles"]:
        for cycle in analysis["cycles"][:MAX_SECTION_ITEMS]:
            cycle_files = "、".join(f"`{path}`" for path in cycle["files"][:6])
            more = f" 等 {cycle['size']} 个文件" if cycle["size"] > 6 else ""
            add(f"- {cycle_files}{more}")
    else:
        add("- 未发现 import 环 ✅")
    add("")

    # ── 孤儿模块 ────────────────────────────────────────────────────
    add("## 孤儿模块（既不被导入也不导入别人）")
    add("")
    if analysis["orphans"]:
        for item in analysis["orphans"][:MAX_SECTION_ITEMS]:
            add(f"- `{item['path']}`")
    else:
        add("- 无 ✅")
    add("")

    # ── 跨社区连接 ──────────────────────────────────────────────────
    add("## 跨社区连接（值得人工确认的耦合）")
    add("")
    if analysis["crossCommunity"]:
        for item in analysis["crossCommunity"][:MAX_SECTION_ITEMS]:
            add(
                f"- {item['relation']}：`{item['source'].split('#')[-1]}`（社区 "
                f"{item['sourceCommunity']}）→ `{item['target'].split('#')[-1]}`"
                f"（社区 {item['targetCommunity']}）"
            )
    else:
        add("- 无 ✅")
    add("")

    # ── 待办热点 ────────────────────────────────────────────────────
    root_path = meta.root_path if meta else ""
    files = [record.path for record in store.iter_files()]
    markers, samples = _scan_markers(root_path, files) if root_path else (Counter(), {})
    add("## 待办热点（TODO / FIXME）")
    add("")
    if markers:
        for path, count in markers.most_common(MAX_MARKER_FILES):
            sample = "；".join(samples.get(path, [])[:2])
            add(f"- `{path}`：{count} 处 — {sample}")
        add(f"- 合计：{sum(markers.values())} 处，分布在 {len(markers)} 个文件")
    else:
        add("- 没有扫描到标记 ✅")
    add("")

    # ── 疑似死代码 ──────────────────────────────────────────────────
    add("## 疑似未被调用（入度为 0 的函数/方法）")
    add("")
    symbols = [
        node
        for node in store.iter_nodes(kinds=list(_SYMBOL_KINDS), limit=2000, order_by_degree=False)
        if int(node.get("inDegree", 0)) == 0 and node.get("file")
    ]
    if symbols:
        for node in symbols[:MAX_SECTION_ITEMS]:
            add(f"- `{node['name']}`（{node['kind']}）— `{node['file']}:{node['start'][0] + 1}`")
        add("- 注意：入口函数（main/handler）天然入度为 0，需要人工确认")
    else:
        add("- 无 ✅")
    add("")

    # ── 复现方式 ────────────────────────────────────────────────────
    add("## 复现方式")
    add("")
    add("```bash")
    add(f"cogen index {meta.target if meta else '<target>'}   # 重新索引")
    add(f"cogen export --repo {identifier}               # 重新生成 graph.json 与本报告")
    add("```")
    add("")

    target.write_text("\n".join(lines), encoding="utf-8")
    return target
