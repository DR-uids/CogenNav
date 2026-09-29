"""后端文案表与语言解析（极简 i18n，不引入 babel/gettext）。

为什么需要它：前端可以切中英文，但 `HTTPException(detail=...)` 与领域异常
（安全校验、clone、parse）的消息原本是硬编码中文，英文界面下会冒出中文报错。

语言来源与优先级：
  1. 请求头 ``Accept-Language``（由 ``api/app.py`` 的中间件写进 ContextVar）；
  2. 后台索引任务：提交任务时把当时的语言捕获下来，任务线程里显式 ``set_locale``
     （线程池不继承调用方的 ContextVar）；
  3. 兜底 ``DEFAULT_LOCALE = "zh"``：CLI / MCP / 既有接口测试的行为完全不变。

因此 ``t()`` 不要求显式传 locale：绝大多数调用点读 ContextVar 就是对的。
"""

from __future__ import annotations

import re
from contextvars import ContextVar, Token
from typing import Any

#: 没有明确语言时的兜底（刻意选中文，保持 CLI、MCP 与既有测试不变）。
DEFAULT_LOCALE = "zh"
#: 支持的语言；新增语言时同步补 MESSAGES 里的一整列。
SUPPORTED_LOCALES: tuple[str, ...] = ("en", "zh")

_ACCEPT_LANGUAGE = re.compile(r"^\s*([A-Za-z]{2,3})")

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        # ── API 层 ──────────────────────────────────────────────────────
        "api.repoNotFound": "Repository not found",
        "api.jobNotFound": "Job not found",
        "api.fileNotFound": "File not found",
        "api.fileNotIndexed": "File is not indexed or does not exist",
        "api.indexInProgress": (
            "Indexing is in progress and the snapshot is not ready yet; retry once it finishes"
        ),
        "api.snapshotGone": "The index snapshot no longer exists; please re-index",
        "api.readFailed": "Read failed: {error}",
        "api.fileTooLarge": "The file exceeds the size limit and cannot be browsed",
        "api.noGrammarForLanguage": (
            "No grammar available for this language ({language}); source browsing only"
        ),
        "api.dirNotFound": "Directory not found: {path}",
        "api.nodeNotFound": "Node not found: {node}",
        "api.rootDirMissing": "Index root directory does not exist: {root}",
        "api.repoBusy": "This repository already has an indexing job running ({jobId})",
        "api.queued": "Queued",
        "api.webNotBuilt": (
            "The web UI is not built yet. During development run `make dev` "
            "(Vite: http://127.0.0.1:5199), or run `make build-web` and let this "
            "service serve the built assets."
        ),
        # ── 输入校验 ────────────────────────────────────────────────────
        "security.what.target": "target",
        "security.what.repoUrl": "repository URL",
        "security.what.ref": "ref",
        "security.what.path": "path",
        "security.empty": "{what} cannot be empty",
        "security.controlChars": "{what} contains control characters",
        "security.whitespace": "{what} cannot contain whitespace",
        "security.leadingDash": "{what} cannot start with '-'",
        "security.tilde": "{what} does not support ~ expansion; use an absolute path",
        "security.doubleColon": "Repository URL contains '::'; unconventional transports are rejected",
        "security.scheme": "Unsupported scheme: {scheme}:// (only http/https/git/ssh)",
        "security.badUrl": (
            "Repository URL must look like http(s)://, git://, ssh:// or user@host:path"
        ),
        "security.pathEmpty": "Path cannot be empty",
        "security.pathControlChars": "Path contains control characters",
        "security.pathNotRelative": "Must be a relative path inside the repository",
        "security.pathDotDot": "Path cannot contain '..'",
        "security.pathEscape": "Path escapes its root: {candidate} is not inside {base}",
        # ── 目标解析 / 克隆 ─────────────────────────────────────────────
        "resolve.targetEmpty": "Repository URL or local path cannot be empty",
        "resolve.doubleColon": "Target contains '::'; unconventional Git transports are rejected",
        "resolve.localMissing": "Local path does not exist or is not a directory: {path}",
        "resolve.unrecognised": (
            "Unrecognised target: pass an existing local directory, "
            "https://github.com/owner/repo, or owner/repo"
        ),
        "clone.notRemote": "Target is not a remote repository",
        "clone.reusing": "Reusing the existing snapshot",
        "clone.cloning": "Shallow-cloning the repository…",
        "clone.done": "Clone complete {commit}",
        "clone.noStderr": "(no stderr output)",
        "clone.gitMissing": "Cannot start git: {error}",
        "clone.timeout": "Clone timed out (over {seconds} seconds)",
        "clone.tooLarge": "Repository size exceeds the limit ({gib} GiB)",
        "clone.failed": "git clone failed (exit {code}): {detail}",
        # ── 解析 ────────────────────────────────────────────────────────
        "parse.languageMissing": "No grammar available for this language: {language}",
        "parse.coreLanguageMissing": (
            "The core wheel has no grammar for this language: {language} (extend with cogen[xlang])"
        ),
        "parse.nodePathTooLong": "nodePath is too long",
        "parse.nodePathInvalid": "Invalid nodePath: {path}",
        "parse.nodeMissing": "Node not found: {path}",
        # ── 目录遍历 / 流水线 ───────────────────────────────────────────
        "walk.dirMissing": "Directory not found: {root}",
        "pipeline.rootMissing": "Index root directory does not exist: {root}",
        "pipeline.metaIndexing": "Indexing",
        "pipeline.targetResolved": "Target resolved",
        "pipeline.cloning": "Shallow-cloning the repository…",
        "pipeline.walking": "Walking {root}",
        "pipeline.foundFiles": "Found {count} files",
        "pipeline.reused": ("Reusing {reused} unchanged files; {changed} need re-parsing"),
        "pipeline.parsing": "Parsing and extracting {count} files…",
        "pipeline.parsed": "Parsed {done}/{total}",
        "pipeline.building": "Building the graph ({count} files of extraction results)…",
        "pipeline.analyzing": "Analysing communities and dependency cycles…",
        "pipeline.summary.base": "{files} files / {loc} lines",
        "pipeline.summary.incremental": " (incremental: {reused} unchanged files reused)",
        "pipeline.summary.parsed": ", parsed {parsed} ({nodes} CST nodes)",
        "pipeline.summary.syntaxErrors": ", {count} with syntax errors",
        "pipeline.summary.parseFailed": ", {count} failed to parse",
        "pipeline.summary.serial": " (process pool unavailable, degraded to a single process)",
        "pipeline.summary.graph": (
            "; graph {nodes} nodes / {edges} edges, {communities} communities"
        ),
        "pipeline.summary.resolvedRate": ", call resolution {rate}",
        "pipeline.summary.cycles": ", {count} import cycles found",
        "pipeline.summary.truncated": ", truncated because {reason}",
        "pipeline.summary.skipped": "; {count} skipped ({top})",
        "pipeline.summary.separator": ", ",
        # ── 任务 ────────────────────────────────────────────────────────
        "jobs.starting": "Starting index",
        "jobs.done": "Indexing finished",
        "jobs.failedShort": "Failed",
        "jobs.openDbFailed": "Cannot open the repository database: {error}",
        # ── AI ──────────────────────────────────────────────────────────
        "llm.notConfigured": "COGEN_LLM_API_KEY is not configured",
        # ── 问答（SSE 事件会直接渲染到对话框里）─────────────────────────
        "ask.systemPrompt": (
            "You are CogenNav's code-navigation assistant. You can only inspect the "
            "indexed code graph through the provided tools; never guess from general "
            "knowledge. Answer requirements:\n"
            "1. Reply in English;\n"
            "2. Lead with the conclusion, then back it up with specifics (call relations, "
            "files, symbol names);\n"
            "3. Always name the symbols you mention so the user can click through;\n"
            "4. If the tools cannot find it, say so plainly; never make it up."
        ),
        "ask.llmNotConfigured": (
            "LLM is not configured (COGEN_LLM_API_KEY); Q&A is unavailable. "
            "The graph and CST features are unaffected."
        ),
        "ask.llmNotConfiguredShort": "LLM is not configured",
        "ask.modelCallFailed": "Model call failed: {error}",
        "ask.emptyModelResult": "The model returned no result",
        "ask.generateFailed": "Failed to generate the answer: {error}",
        "ask.forceConclusion": (
            "Based on the tool results above, give the conclusion directly without "
            "calling more tools."
        ),
        "ask.unknownTool": "Unknown tool: {name}",
        "ask.objectNotFound": "Object not found: {error}",
        "ask.fileNotIndexed": "File not indexed: {path}",
        "ask.missingRoot": "Repository root directory is missing",
        "ask.readFailed": "Read failed: {error}",
        "ask.toolSummary.nodes": "{name}: {nodes} nodes / {edges} edges",
        "ask.toolSummary.matches": "{name}: {count} matches",
        "ask.toolSummary.matches_one": "{name}: {count} match",
        "ask.toolSummary.edges": "{name}: {incoming} in / {outgoing} out",
        "ask.toolSummary.file": "{name}: read {path} lines {start}-{end}",
        "ask.toolSummary.done": "{name}: done",
        # ── 社区命名与架构摘要（名字直接显示在图例/符号卡片里）──────────
        "naming.systemPrompt": (
            "You are a code-architecture analyst. The user gives statistics for one code "
            "community (directories, main symbols, files). Give a short name and a "
            "one-sentence responsibility description in English. "
            'Output JSON only, shaped like {"name": "...", "summary": "..."}; '
            "keep name to a few words and summary to one short sentence, and never "
            "invent modules that do not exist."
        ),
        "naming.summaryPrompt": (
            "You are a code-architecture analyst. From the community and key-node "
            "statistics given, output 3-5 bullet points in English (one per line, each "
            "starting with -) covering this repository's module layout, core abstractions "
            "and notable coupling. Do not invent anything."
        ),
        "naming.communityError": "Community {id}: {error}",
        "naming.snapshotSize": "Community size: {count} symbols",
        "naming.snapshotDirectory": "Main directory: {directory}",
        "naming.snapshotFiles": "Main files:",
        "naming.snapshotSymbols": "Main symbols:",
        "naming.snapshotSymbol": "- {kind} {name} ({file})",
        "naming.heuristicSize": "{count} symbols",
        "naming.heuristicSize_one": "{count} symbol",
        "naming.summaryRepo": "Repository: {target}",
        "naming.summarySize": "{files} files / {loc} lines",
        "naming.summaryCommunities": "Main communities:",
        "naming.summaryCommunity": "- {name} ({count} symbols)",
        "naming.summaryGodNodes": "Key nodes:",
        "naming.summaryGodNode": "- {kind} {name} (degree {degree})",
        "naming.summaryCycles": "Import cycles: {sizes}",
        # ── 图谱细节 ────────────────────────────────────────────────────
        "analyze.orphanReason": "No import relationship with any other file",
        "common.rootDirectory": "(root)",
        "common.unknown": "unknown",
        "common.unknownFile": "unknown file",
        "common.noFiles": "(no files)",
        "common.listSeparator": ", ",
        "llm.noCandidates": "The model returned no candidates",
    },
    "zh": {
        "api.repoNotFound": "仓库不存在",
        "api.jobNotFound": "任务不存在",
        "api.fileNotFound": "文件不存在",
        "api.fileNotIndexed": "文件未索引或不存在",
        "api.indexInProgress": "索引进行中，快照尚未就绪；请等索引完成后重试",
        "api.snapshotGone": "索引快照已不存在，请重新索引",
        "api.readFailed": "读取失败: {error}",
        "api.fileTooLarge": "文件超过大小上限，无法浏览",
        "api.noGrammarForLanguage": "该语言没有可用语法（{language}），仅可浏览源码",
        "api.dirNotFound": "目录不存在: {path}",
        "api.nodeNotFound": "节点不存在: {node}",
        "api.rootDirMissing": "索引根目录不存在: {root}",
        "api.repoBusy": "该仓库已有索引任务在执行（{jobId}）",
        "api.queued": "排队中",
        "api.webNotBuilt": (
            "前端尚未构建。开发时运行 `make dev`（Vite: http://127.0.0.1:5199），"
            "或运行 `make build-web` 后由本服务托管。"
        ),
        "security.what.target": "目标",
        "security.what.repoUrl": "仓库地址",
        "security.what.ref": "ref",
        "security.what.path": "路径",
        "security.empty": "{what}不能为空",
        "security.controlChars": "{what}包含控制字符",
        "security.whitespace": "{what}不能包含空白字符",
        "security.leadingDash": "{what}不能以 '-' 开头",
        "security.tilde": "{what}不支持 ~ 展开，请使用绝对路径",
        "security.doubleColon": "仓库地址包含 '::'，拒绝非常规传输协议",
        "security.scheme": "不允许的协议: {scheme}://（仅支持 http/https/git/ssh）",
        "security.badUrl": "仓库地址必须是 http(s)://、git://、ssh:// 或 user@host:path 形式",
        "security.pathEmpty": "路径不能为空",
        "security.pathControlChars": "路径包含控制字符",
        "security.pathNotRelative": "必须是仓库内的相对路径",
        "security.pathDotDot": "路径不能包含 '..'",
        "security.pathEscape": "路径越界: {candidate} 不在 {base} 之内",
        "resolve.targetEmpty": "仓库地址或本地路径不能为空",
        "resolve.doubleColon": "目标包含 '::'，拒绝非常规 Git 传输协议",
        "resolve.localMissing": "本地路径不存在或不是目录: {path}",
        "resolve.unrecognised": (
            "无法识别的目标：请提供已存在的本地目录、https://github.com/owner/repo 或 owner/repo"
        ),
        "clone.notRemote": "目标不是远端仓库",
        "clone.reusing": "复用已有快照",
        "clone.cloning": "正在浅克隆仓库…",
        "clone.done": "克隆完成 {commit}",
        "clone.noStderr": "（无 stderr 输出）",
        "clone.gitMissing": "无法启动 git: {error}",
        "clone.timeout": "克隆超时（超过 {seconds} 秒）",
        "clone.tooLarge": "仓库体积超过上限（{gib} GiB）",
        "clone.failed": "git clone 失败（exit {code}）: {detail}",
        "parse.languageMissing": "该语言没有可用语法: {language}",
        "parse.coreLanguageMissing": "核心层没有该语言的语法: {language}（可用 cogen[xlang] 扩展层）",
        "parse.nodePathTooLong": "nodePath 过长",
        "parse.nodePathInvalid": "nodePath 非法: {path}",
        "parse.nodeMissing": "节点不存在: {path}",
        "walk.dirMissing": "目录不存在: {root}",
        "pipeline.rootMissing": "索引根目录不存在: {root}",
        "pipeline.metaIndexing": "正在索引",
        "pipeline.targetResolved": "目标已解析",
        "pipeline.cloning": "正在浅克隆仓库…",
        "pipeline.walking": "正在遍历 {root}",
        "pipeline.foundFiles": "已发现 {count} 个文件",
        "pipeline.reused": "复用 {reused} 个未变更文件，需要重新解析 {changed} 个",
        "pipeline.parsing": "正在解析并抽取 {count} 个文件…",
        "pipeline.parsed": "已解析 {done}/{total}",
        "pipeline.building": "正在构建图谱（{count} 个文件的抽取结果）…",
        "pipeline.analyzing": "正在分析社区与依赖环…",
        "pipeline.summary.base": "共 {files} 个文件 / {loc} 行",
        "pipeline.summary.incremental": "（增量：复用 {reused} 个未变更文件）",
        "pipeline.summary.parsed": "，解析 {parsed} 个（{nodes} 个 CST 节点）",
        "pipeline.summary.syntaxErrors": "，{count} 个含语法错误",
        "pipeline.summary.parseFailed": "，{count} 个解析失败",
        "pipeline.summary.serial": "（进程池不可用，已降级单进程）",
        "pipeline.summary.graph": "；图谱 {nodes} 节点 / {edges} 边，社区 {communities} 个",
        "pipeline.summary.resolvedRate": "，调用解析率 {rate}",
        "pipeline.summary.cycles": "，发现 {count} 个 import 环",
        "pipeline.summary.truncated": "，因 {reason} 截断",
        "pipeline.summary.skipped": "；跳过 {count} 项（{top}）",
        "pipeline.summary.separator": "、",
        "jobs.starting": "开始索引",
        "jobs.done": "索引完成",
        "jobs.failedShort": "失败",
        "jobs.openDbFailed": "无法打开仓库数据库: {error}",
        "llm.notConfigured": "未配置 COGEN_LLM_API_KEY",
        "ask.systemPrompt": (
            "你是 CogenNav 的代码仓库导航助手。你只能通过提供的工具查询已索引的代码图谱，"
            "不要凭常识猜测。回答要求：\n"
            "1. 用简体中文；\n"
            "2. 结论先行，再用要点说明依据（调用关系、文件、符号名都要具体）；\n"
            "3. 涉及符号时写出它的名字，便于用户点击跳转；\n"
            "4. 工具查不到就直说，不要编造。"
        ),
        "ask.llmNotConfigured": "未配置 LLM（COGEN_LLM_API_KEY），问答不可用；图谱与 CST 功能不受影响。",
        "ask.llmNotConfiguredShort": "未配置 LLM",
        "ask.modelCallFailed": "调用模型失败：{error}",
        "ask.emptyModelResult": "模型没有返回结果",
        "ask.generateFailed": "生成回答失败：{error}",
        "ask.forceConclusion": "请基于以上工具结果直接给出结论，不要再调用工具。",
        "ask.unknownTool": "未知工具: {name}",
        "ask.objectNotFound": "找不到对象: {error}",
        "ask.fileNotIndexed": "文件未索引: {path}",
        "ask.missingRoot": "缺少仓库根目录",
        "ask.readFailed": "读取失败: {error}",
        "ask.toolSummary.nodes": "{name}: {nodes} 个节点 / {edges} 条边",
        "ask.toolSummary.matches": "{name}: {count} 条匹配",
        "ask.toolSummary.matches_one": "{name}: {count} 条匹配",
        "ask.toolSummary.edges": "{name}: {incoming} 入边 / {outgoing} 出边",
        "ask.toolSummary.file": "{name}: 读取 {path} 第 {start}-{end} 行",
        "ask.toolSummary.done": "{name}: 完成",
        "naming.systemPrompt": (
            "你是代码架构分析助手。用户会给出一个代码社区的统计（目录、主要符号、文件），"
            "你要用简体中文给出简短命名与一句话职责描述。"
            '只输出 JSON，形如 {"name": "...", "summary": "..."}；'
            "name 不超过 12 个字，summary 不超过 60 个字，不要臆造不存在的模块。"
        ),
        "naming.summaryPrompt": (
            "你是代码架构分析助手。根据给定的社区与关键节点统计，"
            "用简体中文输出 3-5 条要点（每条一行，以 - 开头），"
            "说明这个仓库的模块划分、核心抽象与值得注意的耦合。不要臆造。"
        ),
        "naming.communityError": "社区 {id}: {error}",
        "naming.snapshotSize": "社区规模：{count} 个符号",
        "naming.snapshotDirectory": "主要目录：{directory}",
        "naming.snapshotFiles": "主要文件：",
        "naming.snapshotSymbols": "主要符号：",
        "naming.snapshotSymbol": "- {kind} {name}（{file}）",
        "naming.heuristicSize": "{count} 个符号",
        "naming.heuristicSize_one": "{count} 个符号",
        "naming.summaryRepo": "仓库：{target}",
        "naming.summarySize": "文件 {files} 个 / {loc} 行",
        "naming.summaryCommunities": "主要社区：",
        "naming.summaryCommunity": "- {name}（{count} 个符号）",
        "naming.summaryGodNodes": "关键节点：",
        "naming.summaryGodNode": "- {kind} {name}（度数 {degree}）",
        "naming.summaryCycles": "存在 import 环：{sizes}",
        "analyze.orphanReason": "没有 import 关系的文件",
        "common.rootDirectory": "(根目录)",
        "common.unknown": "未知",
        "common.unknownFile": "未知文件",
        "common.noFiles": "(无文件)",
        "common.listSeparator": "、",
        "llm.noCandidates": "模型没有返回任何候选结果",
    },
}

#: check_text/check_repo_relative_path 的 ``what`` 角色 → 文案键。
_WHAT_KEYS: dict[str, str] = {
    "target": "security.what.target",
    "repoUrl": "security.what.repoUrl",
    "ref": "security.what.ref",
    "path": "security.what.path",
}

_current: ContextVar[str] = ContextVar("cogen_locale", default=DEFAULT_LOCALE)


def resolve_locale(header: str | None) -> str:
    """从 ``Accept-Language`` 里取首选语言；不认识的一律回落到默认语言。"""
    for item in (header or "").split(","):
        match = _ACCEPT_LANGUAGE.match(item)
        if not match:
            continue
        tag = match.group(1).lower()
        if tag in SUPPORTED_LOCALES:
            return tag
        # zh-Hans / zh-CN 之类的前缀匹配
        for candidate in SUPPORTED_LOCALES:
            if tag.startswith(candidate):
                return candidate
        return DEFAULT_LOCALE
    return DEFAULT_LOCALE


def set_locale(locale: str) -> Token[str]:
    """设置当前上下文语言（返回值交给 :func:`reset_locale`）。"""
    return _current.set(locale if locale in SUPPORTED_LOCALES else DEFAULT_LOCALE)


def reset_locale(token: Token[str]) -> None:
    _current.reset(token)


def current_locale() -> str:
    return _current.get()


def what_label(what: str) -> str:
    """把 ``what`` 角色翻成当前语言的标签（未知角色原样返回）。"""
    key = _WHAT_KEYS.get(what)
    return t(key) if key else what


def t(key: str, *, locale: str | None = None, **params: Any) -> str:
    """取文案并插值；键不存在时回落到默认语言，再不存在就原样返回键名。

    ``count == 1`` 且存在 ``<key>_one`` 时用单数形式（英文需要，中文同形）。
    """
    target = locale or current_locale()
    catalog = MESSAGES.get(target) or MESSAGES[DEFAULT_LOCALE]
    template = catalog.get(key) or MESSAGES[DEFAULT_LOCALE].get(key)
    if template is None:
        return key
    if params.get("count") == 1:
        template = catalog.get(f"{key}_one") or template
    if not params:
        return template
    return re.sub(
        r"\{(\w+)\}",
        lambda match: str(params[match.group(1)]) if match.group(1) in params else match.group(0),
        template,
    )
