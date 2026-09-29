"""CogenNav 命令行入口。

M0 交付：`serve`（本地 Web 服务）与 `status`。
其余子命令在后续里程碑落地，此处给出明确的占位与归属。
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from . import __version__
from .config import get_settings
from .graph.store import Store


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="cogen")
def main() -> None:
    """CogenNav — 代码仓库 CST 解析与知识图谱导航。"""


@main.command()
@click.option("--host", default=None, help="监听地址（默认 127.0.0.1）")
@click.option("--port", default=None, type=int, help="监听端口（默认 8765）")
@click.option("--reload", is_flag=True, help="开发模式热重载")
def serve(host: str | None, port: int | None, reload: bool) -> None:
    """启动本地 Web 服务（解析引擎 + 前端）。"""
    import uvicorn

    settings = get_settings()
    settings.ensure_dirs()
    uvicorn.run(
        "cogen.api.app:app",
        host=host or settings.host,
        port=port or settings.port,
        reload=reload,
        log_level=settings.log_level,
    )


@main.command()
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出")
def status(as_json: bool) -> None:
    """显示配置与运行期数据目录状态。"""
    settings = get_settings()
    info: dict[str, Any] = {
        "version": __version__,
        "home": str(settings.root),
        "home_exists": settings.root.exists(),
        "host": settings.host,
        "port": settings.port,
        "indexed_repos": [],
        "limits": {
            "max_files": settings.max_files,
            "max_file_bytes": settings.max_file_bytes,
            "max_total_bytes": settings.max_total_bytes,
            "clone_timeout_s": settings.clone_timeout_s,
            "parse_chunk_timeout_s": settings.parse_chunk_timeout_s,
            "parse_workers": settings.effective_parse_workers(),
        },
        "llm": {"configured": settings.llm_configured, "model": settings.llm_model},
    }
    if settings.db_dir.exists():
        info["indexed_repos"] = sorted(p.stem for p in settings.db_dir.glob("*.sqlite"))

    if as_json:
        click.echo(json.dumps(info, indent=2, ensure_ascii=False))
        return

    click.echo(f"cogen {info['version']}")
    click.echo(f"  COGEN_HOME : {info['home']}{'' if info['home_exists'] else '  (未创建)'}")
    click.echo(f"  服务地址   : http://{info['host']}:{info['port']}")
    click.echo(f"  已索引仓库 : {len(info['indexed_repos'])}")
    click.echo(
        f"  LLM        : {'已配置 ' + settings.llm_model if settings.llm_configured else '未配置（AI 功能降级）'}"
    )


@main.command()
@click.argument("target")
@click.option("--ref", default=None, help="分支 / 标签 / commit")
@click.option("--subdir", default=None, help="只索引仓库内的某个子目录")
@click.option("--json", "as_json", is_flag=True, help="以 JSON 输出结果摘要")
def index(target: str, ref: str | None, subdir: str | None, as_json: bool) -> None:
    """索引一个仓库（本地路径或 GitHub 地址）。"""
    from .graph.store import open_store
    from .ingest.pipeline import index_repo
    from .ingest.resolve import repo_id, resolve_target
    from .jobs import ConsoleProgress
    from .security import UnsafeTargetError

    settings = get_settings()
    settings.ensure_dirs()

    try:
        spec = resolve_target(target, ref=ref, subdir=subdir)
    except UnsafeTargetError as exc:
        click.secho(f"目标不合法：{exc}", fg="red", err=True)
        sys.exit(2)

    rid = repo_id(spec)
    progress = ConsoleProgress(lambda line: click.echo(line, err=True) if not as_json else None)
    try:
        with open_store(settings, rid) as store:
            index_repo(spec, settings, store, progress)  # type: ignore[arg-type]
            meta = store.load_repo_meta()
            extra = store.get_meta()
    except Exception as exc:  # 把失败原因回显给用户，而不是抛栈
        click.secho(f"索引失败：{type(exc).__name__}: {exc}", fg="red", err=True)
        sys.exit(1)

    if meta is None:
        click.secho("索引失败：没有写入仓库元信息", fg="red", err=True)
        sys.exit(1)

    if as_json:
        click.echo(
            json.dumps(
                {**meta.to_api(), "parse": extra.get("parse"), "skipped": extra.get("skipped")},
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    click.echo(f"repoId : {meta.repo_id}")
    click.echo(f"状态   : {meta.state}")
    click.echo(f"文件   : {meta.file_count} 个 / {meta.loc} 行")
    click.echo(f"语言   : {', '.join(f'{k}={v}' for k, v in list(meta.languages.items())[:8])}")
    click.echo(f"快照   : {meta.root_path}")
    click.echo(f"数据库 : {settings.db_dir / (meta.repo_id + '.sqlite')}")
    if meta.message:
        click.echo(f"摘要   : {meta.message}")


@main.command("export")
@click.option("--repo", "repo_id", default=None, help="repoId（省略则用最近索引的仓库）")
@click.option("--out", "output", default=None, type=click.Path(), help="graph.json 输出路径")
@click.option(
    "--format",
    "formats",
    default="all",
    type=click.Choice(["all", "json", "md", "html"], case_sensitive=False),
    help="导出格式（默认 all）",
)
@click.option("--json", "as_json", is_flag=True, help="以 JSON 打印结果路径")
def export_cmd(repo_id: str | None, output: str | None, formats: str, as_json: bool) -> None:
    """导出图谱产物：graph.json / GRAPH_REPORT.md / graph.html（--format 选一种）。"""
    want_json = formats in ("all", "json")
    want_report = formats in ("all", "md")
    want_html = formats in ("all", "html")
    from .export.html_export import export_html
    from .export.json_export import export_json
    from .export.report import generate_report
    from .graph.store import db_path_for, list_repo_metas
    from .mcp.server import resolve_repo_id

    settings = get_settings()
    settings.ensure_dirs()
    try:
        rid = resolve_repo_id(settings, repo_id)
    except SystemExit as exc:
        click.secho(str(exc), fg="yellow", err=True)
        raise SystemExit(2) from exc

    with Store(db_path_for(settings, rid)) as store:
        if store.load_repo_meta() is None:
            click.secho(f"仓库不存在: {rid}", fg="red", err=True)
            raise SystemExit(2)
        output_path = __import__("pathlib").Path(output) if output else None
        graph_path = (
            export_json(store, settings, output=output_path, repo_id=rid) if want_json else None
        )
        report_path = generate_report(store, settings) if want_report else None
        html_path = export_html(store, settings, repo_id=rid) if want_html else None

    if as_json:
        click.echo(
            json.dumps(
                {
                    "repoId": rid,
                    "graph": str(graph_path) if graph_path else None,
                    "report": str(report_path) if report_path else None,
                    "html": str(html_path) if html_path else None,
                },
                ensure_ascii=False,
            )
        )
        return
    if graph_path:
        click.echo(f"graph.json      -> {graph_path}")
    if report_path:
        click.echo(f"GRAPH_REPORT.md -> {report_path}")
    if html_path:
        click.echo(f"graph.html      -> {html_path}")
    click.echo(f"（共 {len(list_repo_metas(settings))} 个已索引仓库）")


@main.command()
@click.argument("question")
@click.option("--repo", "repo_id", default=None, help="repoId（省略则用最近索引的仓库）")
@click.option("--show-tools/--hide-tools", default=True, help="是否打印工具调用轨迹")
def ask(question: str, repo_id: str | None, show_tools: bool) -> None:
    """对已索引的仓库提问（需要 COGEN_LLM_API_KEY）。"""
    import asyncio

    from .ai import ask as ask_module
    from .ai import llm
    from .graph.store import db_path_for
    from .mcp.server import resolve_repo_id

    settings = get_settings()
    try:
        rid = resolve_repo_id(settings, repo_id)
    except SystemExit as exc:
        click.secho(str(exc), fg="yellow", err=True)
        raise SystemExit(2) from exc

    if not llm.is_configured(settings):
        click.secho(
            "未配置 LLM：请设置 COGEN_LLM_API_KEY（可选 COGEN_LLM_BASE_URL/_MODEL）。"
            "图谱与 CST 功能不受影响。",
            fg="yellow",
            err=True,
        )
        raise SystemExit(2)

    async def run() -> int:
        with Store(db_path_for(settings, rid)) as store:
            meta = store.load_repo_meta()
            async for event in ask_module.answer(
                settings,
                store,
                question,
                root_path=meta.root_path if meta else None,
            ):
                kind = event.get("type")
                if kind == "tool" and show_tools:
                    arguments = event.get("arguments") or {}
                    click.secho(f"  · 调用 {event.get('name')} {arguments}", fg="cyan", err=True)
                elif kind == "tool_result" and show_tools:
                    click.secho(f"    → {event.get('summary')}", fg="cyan", err=True)
                elif kind == "delta":
                    click.echo(str(event.get("text", "")), nl=False)
                elif kind == "done":
                    click.echo()
                    citations = event.get("citations") or []
                    if citations:
                        click.echo()
                        click.secho("引用：", fg="bright_black", err=True)
                        for item in citations[:10]:
                            click.secho(
                                f"  - {item.get('name')}（{item.get('kind')}）"
                                f" {item.get('file') or ''}",
                                fg="bright_black",
                                err=True,
                            )
                elif kind == "error":
                    click.secho(str(event.get("message")), fg="red", err=True)
                    return 1
        return 0

    raise SystemExit(asyncio.run(run()))


@main.command()
@click.option("--stdio", "transport", flag_value="stdio", default=True, help="stdio 传输（默认）")
@click.option("--http", "transport", flag_value="http", help="Streamable HTTP 传输")
@click.option("--port", default=8770, type=int, help="HTTP 模式端口")
@click.option("--repo", "repo_id", default=None, help="repoId（省略则用最近索引的仓库）")
def mcp(transport: str, port: int, repo_id: str | None) -> None:
    """启动 MCP Server，供 AI 编程助手查询图谱（只读工具集）。"""
    from .mcp.server import run_mcp

    try:
        run_mcp(repo_id=repo_id, transport=transport, port=port)
    except SystemExit as exc:
        click.secho(str(exc), fg="yellow", err=True)
        raise SystemExit(2) from exc


@main.command()
@click.argument("languages", nargs=-1)
def warm(languages: tuple[str, ...]) -> None:
    """预取扩展语言（cogen[xlang]）的语法解析器。

    扩展层首次解析某语言会联网下载**原生**解析器，这里把它提前下好并缓存到工作区内。
    """
    import os

    from .parse.languages import CORE_GRAMMARS, load_xlang_language

    settings = get_settings()
    cache_dir = os.environ.get("TREE_SITTER_LANGUAGE_PACK_CACHE_DIR") or str(
        settings.cache_dir / "grammars"
    )
    # 必须在导入语言包之前定好缓存目录，否则它会写到家目录（沙箱会拒绝）
    os.environ.setdefault("TREE_SITTER_LANGUAGE_PACK_CACHE_DIR", cache_dir)
    settings.ensure_dirs()

    from .parse.languages import xlang_available

    if not xlang_available():
        click.secho('扩展语言层未安装。安装：pip install -e ".[xlang]"', fg="yellow", err=True)
        click.echo("核心层已内置：" + "、".join(sorted(CORE_GRAMMARS)))
        sys.exit(2)

    targets = [item.strip() for item in languages if item.strip()]
    if not targets:
        click.secho("请显式指定要预取的语言，例如：cogen warm rust toml", fg="yellow", err=True)
        sys.exit(2)

    try:
        from tree_sitter_language_pack import configure

        configure({"cache_dir": cache_dir})
    except Exception:  # pragma: no cover - 该包接口变动时退化为环境变量方式
        pass

    failed: list[str] = []
    for language in targets:
        try:
            load_xlang_language(language)
            click.secho(f"  ✓ {language}", fg="green")
        except Exception as exc:
            failed.append(language)
            click.secho(f"  ✗ {language}: {type(exc).__name__}: {exc}", fg="red", err=True)

    click.echo(f"缓存目录：{cache_dir}")
    if failed:
        click.secho(f"失败 {len(failed)} 个：{', '.join(failed)}", fg="red", err=True)
        sys.exit(1)
    click.echo(f"已就绪 {len(targets)} 个语言")


if __name__ == "__main__":
    main()
