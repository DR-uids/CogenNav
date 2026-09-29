/**
 * 英文文案表（**默认语言**，也是键的唯一定义处）。
 *
 * 约定：
 *  - `zh` 表必须与本表键一一对应（`Record<MessageKey, string>` 会在编译期强制）；
 *  - 占位符用 `{name}`，由 `i18n/index.ts` 的 `t()` 插值；
 *  - 复数：需要区分单复数的键再补一个 `<key>_one`，`t()` 在 `count === 1` 时自动改用；
 *  - 键名按区域前缀分组（topbar / sidebar / job / repoList / cstFiles …）。
 */
export const en = {
  /* ── 外壳 ─────────────────────────────────────────────────────────── */
  "app.title": "CogenNav — code repository navigation",
  "app.viewContent": "View content",
  "app.indexDone": "Indexing finished",

  "topbar.tagline": "code repository CST parsing · knowledge graph navigation",
  "topbar.connecting": "Connecting to backend…",
  "topbar.offline": "Backend not running (make dev)",
  "topbar.llmConfigured": "LLM configured",
  "topbar.llmUnconfigured": "LLM not configured",

  "locale.aria": "Interface language",
  "locale.en": "English",
  "locale.zh": "中文",

  "viewTabs.aria": "View switcher",
  "view.unknown": "Unknown view: {id}",

  /* ── 侧栏 / 仓库 ──────────────────────────────────────────────────── */
  "sidebar.repos": "Indexed repositories",
  "sidebar.phase": "Phase: {phase}",
  "sidebar.notice": "Binds 127.0.0.1 only · never runs analysed code · parsing runs in a separate process",

  "repoInput.label": "Repository URL / local path",
  "repoInput.placeholder": "https://github.com/owner/repo",
  "repoInput.submit": "Index",
  "repoInput.submitting": "Submitting…",
  "repoInput.required": "Enter a repository URL or an absolute local path first",
  "repoInput.submitFailed": "Submission failed, please retry",
  "repoInput.hint": "Accepts git URLs and absolute local paths; indexing progress streams live over SSE.",

  "repoList.loading": "Loading repositories…",
  "repoList.loadFailed": "Failed to load repositories",
  "repoList.empty": "No repositories indexed yet.",
  "repoList.deleteFailed": "Delete failed",
  "repoList.reindexFailed": "Re-index failed",
  "repoList.reindex": "Re-index",
  "repoList.reindexTitle": "Index the same target again (re-clones if the snapshot was cleaned up)",
  "repoList.reindexAria": "Re-index repository {name}",
  "repoList.delete": "Delete",
  "repoList.deleteAria": "Delete repository {name}",
  "repoList.fileCount": "{count} files",
  "repoList.fileCount_one": "{count} file",
  "repoList.lineCount": "{count} lines",
  "repoList.lineCount_one": "{count} line",

  "comingSoon.delivered": "This view ships in milestone {milestone}; currently an M0 skeleton.",

  /* ── 索引进度 ─────────────────────────────────────────────────────── */
  "job.phase.resolve": "Resolving target",
  "job.phase.clone": "Cloning repository",
  "job.phase.walk": "Walking files",
  "job.phase.parse": "Parsing syntax",
  "job.phase.extract": "Extracting symbols",
  "job.phase.build": "Building graph",
  "job.phase.analyze": "Analysing communities",
  "job.phase.name": "Generating names",
  "job.phase.done": "Done",
  "job.state.queued": "Queued",
  "job.state.running": "Running",
  "job.state.done": "Done",
  "job.state.error": "Failed",
  "job.idle": "Idle",
  "job.waiting": "Waiting for a job…",
  "job.progressAria": "Indexing progress",
  "job.failed": "Indexing failed",
  "job.awaitingProgress": "Waiting for progress…",

  /* ── 视图元信息（Tab 标签与说明）──────────────────────────────────── */
  "view.tree.label": "Directory tree",
  "view.tree.blurb":
    "Directory tree + treemap: area by LOC, colour by language; the language bar switches between file count and LOC.",
  "view.cst.label": "CST syntax tree",
  "view.cst.blurb":
    "Lazy tree-sitter CST drill-down: node types, field names, line/column ranges, linked two-way with the highlighted source.",
  "view.graph.label": "Knowledge graph",
  "view.graph.blurb":
    "Three modes: Sigma (WebGL) force-directed overview, React Flow + dagre layered DAG, and downstream impact closure.",
  "view.ask.label": "AI Q&A",
  "view.ask.blurb":
    "Ask the graph (who calls X / what does changing X affect); answers carry clickable symbol citations.",

  /* ── 目录树视图 ───────────────────────────────────────────────────── */
  "dirTree.title": "Directories",
  "dirTree.total": "{files} / {loc}",
  "dirTree.oneLevel": "Top level only",
  "dirTree.pickRepo":
    "Pick a repository in the Repositories list on the left; its directory structure and treemap show up here.",
  "dirTree.indexing":
    "This repository is {state}; the directory tree can only be read once indexing finishes, and then loads automatically.",
  "dirTree.loading": "Loading directory tree…",
  "dirTree.loadFailed": "Failed to load directory tree: {error}",
  "dirTree.missingNode": "The backend returned no directory tree (node missing).",
  "dirTree.treemapTitle": "Treemap",
  "dirTree.treemapHint":
    "Area = LOC, colour = language; click a rectangle to open the file, click a directory to select it.",
  "dirTree.selectedDir": "Selected directory: {path}",
  "dirTree.kind.dir": "directory",
  "dirTree.kind.file": "file",
  "dirTree.rowAria": "{type} {path}",
  "dirTree.root": "root",
  "dirTree.notLoaded": "collapsed",
  "dirTree.collapse": "Collapse",
  "dirTree.expand": "Expand",

  "unit.lines": "{count} lines",
  "unit.lines_one": "{count} line",
  "unit.files": "{count} files",
  "unit.files_one": "{count} file",
  "unit.symbols": "{count} symbols",
  "unit.symbols_one": "{count} symbol",
  "unit.errors": "{count} errors",
  "unit.errors_one": "{count} error",
  "unit.nodes": "{count} nodes",
  "unit.nodes_one": "{count} node",
  "unit.toolCalls": "{count} tool calls",
  "unit.toolCalls_one": "{count} tool call",
  "unit.communities": "{count} communities",
  "unit.communities_one": "{count} community",

  "langBar.title": "Language distribution",
  "langBar.metricAria": "Language metric",
  "langBar.byLoc": "By LOC",
  "langBar.byFiles": "By files",
  "langBar.barAria": "Language share",
  "langBar.empty": "No language statistics yet.",
  "langBar.totalFiles": "{count} files",
  "langBar.totalFiles_one": "{count} file",

  "treemap.fileAria": "File {path}",
  "treemap.svgAria": "Treemap sized by LOC, coloured by language",
  "treemap.empty": "Nothing to show (no code lines counted for this repository yet).",
  "treemap.language": "Language: ",
  "treemap.loc": "LOC: ",
  "treemap.files": "Files: ",
  "treemap.symbols": "Symbols: ",

  "language.other": "Other",

  /* ── CST 视图 ─────────────────────────────────────────────────────── */
  "cstFiles.title": "Files",
  "cstFiles.noRepo": "No repository selected",
  "cstFiles.loadingShort": "Loading…",
  "cstFiles.showingOfTotal": "Showing {shown} / {total} files",
  "cstFiles.total": "{count} files in total",
  "cstFiles.total_one": "{count} file in total",
  "cstFiles.searchPlaceholder": "Search paths…",
  "cstFiles.searchAria": "Search file paths",
  "cstFiles.pickRepo":
    "Pick a repository in the Repositories list on the left; its files are listed here.",
  "cstFiles.loadFailed": "Failed to load the file list",
  "cstFiles.noMatch": "No files match “{query}”.",
  "cstFiles.empty": "This repository has no browsable files.",
  "cstFiles.loading": "Loading file list…",
  "cstFiles.loadMore": "Load more ({remaining} left)",
  "cstFiles.errorTitle": "{path} ({error})",
  "cstFiles.parseFailed": "parse failed",

  "cstTree.title": "CST syntax tree",
  "cstTree.collapse": "Collapse",
  "cstTree.expand": "Expand",
  "cstTree.missing": "missing",
  "cstTree.loading": "Loading…",
  "cstTree.subtreeFailedPlain": "Failed to load subtree",
  "cstTree.totalNodes": "{count} nodes",
  "cstTree.totalNodes_one": "{count} node",
  "cstTree.subtreeFailed": "Failed to load subtree: {error}",
  "cstTree.retry": "Retry",
  "cstTree.depth": "Depth {depth}",
  "cstTree.collapseAll": "Collapse all",
  "cstTree.pickFile":
    "Pick a file on the left to see its CST syntax tree; expand nodes to drill down on demand.",
  "cstTree.parsing": "Parsing syntax tree…",
  "cstTree.noGrammarTitle": "No grammar available for this language",
  "cstTree.noGrammarBody":
    "The backend ships no tree-sitter parser for this language, so the CST cannot be shown; the source on the right is still readable.",
  "cstTree.notFound": "File not found or outside the repository: ",
  "cstTree.loadFailed": "Failed to load the syntax tree: ",
  "cstTree.unknownError": "Unknown error",

  "cstSource.title": "Source",
  "cstSource.selectionTitle": "Source range of the selected node",
  "cstSource.truncatedLines": "Only the first {limit} lines are rendered ({total} lines in total)",
  "cstSource.locating": "Locating node…",
  "cstSource.pickFile":
    "Pick a file to browse its source here; selecting a tree node highlights its range.",
  "cstSource.loading": "Reading source…",
  "cstSource.loadFailed": "Failed to load the source",
  "cstSource.truncated": "The file is too large; the source is shown truncated.",
  "cstSource.emptyFile": "This file is empty.",

  /* ── 知识图谱视图 ─────────────────────────────────────────────────── */
  "graphMode.force": "Force-directed",
  "graphMode.dag": "Layered DAG",
  "graphMode.impact": "Impact",
  "graphDirection.both": "Both",
  "graphDirection.out": "Downstream",
  "graphDirection.in": "Upstream",
  "dagDirection.both": "Both ways",
  "dagDirection.out": "Downstream (out)",
  "dagDirection.in": "Upstream (in)",

  "graphView.modeAria": "Graph mode",
  "graphView.directionAria": "Neighbourhood direction",
  "graphView.focusNode": "Focus node",
  "graphView.noSelection": "(none)",
  "graphView.notice":
    "Layered DAG and impact call /graph/neighbors and /graph/impact: only the relation filter applies there; node kind and confidence apply to the force-directed overview.",
  "graphView.stats": "{mode} · {nodes} / {edges}",
  "graphView.focus": "Focus: ",
  "graphView.backToGlobal": "Back to global",
  "graphView.refreshing": "Refreshing…",
  "graphView.neighborDepth": "Neighbour depth 3",
  "graphView.impactDepth": "Downstream depth 3",
  "graphView.limit": "Limit {limit}",
  "graphView.truncated": "Results truncated: showing the first {shown} of {total} nodes.",
  "graphView.showMore": "Show more",
  "graphView.largeWarning":
    "More than {limit} nodes: the browser may get noticeably slow. Narrow the filters first, or switch to the layered DAG for a local view.",
  "graphView.pickRepo":
    "Pick a repository in the Repositories list on the left; its symbol knowledge graph is drawn here.",
  "graphView.needFocus":
    "{mode} mode needs a focus symbol: pick one with the search box above, or choose a god node from the dropdown.",
  "graphView.loading": "Loading graph…",
  "graphView.loadFailed": "Failed to load the graph",
  "graphView.empty":
    "No symbol matches the current filters. Loosen node kind / relation / confidence, or run an index first.",
  "graphView.inspector": "Inspector",
  "graphView.clear": "Clear",
  "graphView.inspectorEmpty":
    "Click a node in the graph to see its details: definition, snippet and in/out edges; edges can be followed by clicking them.",

  "graphSearch.placeholder": "Search symbols (name / qualified / path)…",
  "graphSearch.aria": "Search symbols",
  "graphSearch.searching": "Searching…",
  "graphSearch.failed": "Search failed",
  "graphSearch.noMatch": "No symbol matches “{query}”.",
  "graphSearch.hits": "{total} hits, showing the first {shown}",
  "graphSearch.hits_one": "{total} hit, showing the first {shown}",

  "filter.title": "Filters",
  "filter.reset": "Reset",
  "filter.noData": "No data",
  "filter.nodes": "nodes {count}",
  "filter.edges": "edges {count}",
  "filter.kind": "Node kind",
  "filter.kindHint": "All checked = no kind filter",
  "filter.relation": "Relation",
  "filter.relationHint": "All checked = no relation filter",
  "filter.confidence": "Confidence",
  "filter.hideAmbiguous": "Hide AMBIGUOUS",
  "filter.hideAmbiguousHint":
    "AMBIGUOUS edges are heuristic guesses and stay out of the main view by default.",
  "filter.community": "Community",
  "filter.allCommunities": "All communities",
  "filter.communityOption": "{name} ({size})",
  "filter.communityActive": "Filtered by community",

  "legend.aria": "Legend",
  "legend.hops": "Impact hops",
  "legend.community": "Community",
  "legend.hop": "Hop {hop}",
  "legend.focus": "Focus",
  "legend.impactHint":
    "Warmer colours are closer to the focus: these symbols may be affected by the change.",
  "legend.noCommunities": "The backend has no community partition yet.",
  "legend.communityTitle": "{name} ({size} symbols{namedBy})",
  "legend.namedByLlm": ", named by LLM",
  "legend.namedByHeuristic": ", named heuristically",
  "legend.ungrouped": "No community",
  "legend.sizeIsDegree": "Size = degree",
  "legend.strokeIs": "Stroke =",
  "legend.palette": "{count} colours in the palette, then it cycles.",

  "impact.title": "Files to regression-test",
  "impact.fileCount": "{count} files",
  "impact.fileCount_one": "{count} file",
  "impact.nodeCount": "affected symbols {count}",
  "impact.root": "Root symbol: {name}",
  "impact.truncated": "The closure is too large and was truncated; the list below is incomplete.",
  "impact.empty": "No downstream dependencies: changing this symbol needs no other file retested.",
  "impact.fileTitle": "{path} ({count} symbols hit)",

  "dag.summary": "{direction} · {nodes} · {ranks}",
  "dag.rank": "{count} layers",
  "dag.rank_one": "{count} layer",
  "dag.focusLayer": " · focus on layer {layer}",

  "symbol.pickRepo": "Pick a repository on the left.",
  "symbol.loading": "Reading symbol details…",
  "symbol.loadFailed": "Failed to load symbol details",
  "symbol.community": "Community: {name}",
  "symbol.degree": "degree {degree} (in {inDegree} / out {outDegree})",
  "symbol.definition": "Definition",
  "symbol.noDefinition": "The backend reported no definition location.",
  "symbol.openCst": "View syntax tree",
  "symbol.incoming": "Incoming",
  "symbol.incomingEmpty": "Nothing calls or references it.",
  "symbol.outgoing": "Outgoing",
  "symbol.outgoingEmpty": "It calls or references no other symbol.",

  "graph.rendererLoading": "Loading graph renderer…",
  "graph.forceAria": "Force-directed graph (sigma renderer)",

  /* ── AI 问答视图 ──────────────────────────────────────────────────── */
  "ask.quick.entry": "What is the entry point of this repository?",
  "ask.quick.whoCalls": "Who calls Engine.run?",
  "ask.quick.impact": "Which files does changing fmt affect?",
  "ask.quick.godNodes": "What are the core abstractions (god nodes) here?",
  "ask.toolPending": "(waiting for the tool to return…)",
  "ask.role.user": "You",
  "ask.role.assistant": "Assistant",
  "ask.retrieving": "Retrieving from the graph…",
  "ask.stopped": "Generation stopped (the text above is what was received).",
  "ask.status.title": "AI status",
  "ask.status.noRepo":
    "Pick a repository in the Repositories list on the left before asking questions about its graph.",
  "ask.status.loading": "Reading LLM status…",
  "ask.status.model": "Model",
  "ask.status.modelUnset": "(not set)",
  "ask.status.rag": "RAG",
  "ask.status.redact": "Redaction",
  "ask.status.on": "on",
  "ask.status.off": "off",
  "ask.status.tools": "read-only tools: {count}",
  "ask.status.toolsNone": "(none)",
  "ask.llmWarning":
    "LLM is not configured (COGEN_LLM_API_KEY is empty): Q&A is unavailable and the input is disabled, though past messages stay readable. The directory tree, CST and knowledge graph are unaffected. Community naming and the architecture summary still work and fall back to deterministic heuristics.",
  "ask.quick.title": "Quick questions",
  "ask.community.title": "Community naming / architecture summary",
  "ask.community.hint":
    "Community names are written back into the graph, so the legend and symbol cards follow; without an LLM the backend names them heuristically (results mark namedBy).",
  "ask.community.naming": "Naming…",
  "ask.community.name": "Name communities",
  "ask.community.force": "Force rename",
  "ask.community.updated": "Updated {count} communities · {source}",
  "ask.community.byLlm": "named by LLM",
  "ask.community.byHeuristic": "named heuristically",
  "ask.community.unknown": "unknown",
  "ask.summary.generating": "Generating…",
  "ask.summary.button": "Generate architecture summary",
  "ask.summary.source": "Source: {source}",
  "ask.summary.sourceHeuristic": "heuristic (not configured, or the call failed)",
  "ask.emptyNoRepo":
    "Pick a repository, then ask things like “who calls X” or “which files does changing X affect”; symbols in the answer open straight in the graph.",
  "ask.empty":
    "No question yet. Start from a quick question on the left, or just type below. Answers list the tools they ran and jumpable symbol citations.",
  "ask.placeholder":
    "Ask something, e.g. “who calls Engine.run?” (Enter to send, Shift+Enter for a new line)",
  "ask.placeholderDisabled": "LLM is not configured; Q&A input is disabled",
  "ask.send": "Send",
  "ask.generating": "Generating…",
  "ask.stop": "Stop",
  "ask.inputHint":
    "Enter to send · Shift+Enter for a new line · read-only tools, the repository is never modified",
  "ask.citations": "Citations",
  "ask.citationsEmpty":
    "Symbols cited by answers are listed here; click one to jump to the graph and focus it.",
  "ask.traces": "Tool trace",
  "ask.tracesEmpty":
    "The read-only tools the assistant ran (graph queries, symbol search, source snippets…) are listed here in order.",
  "ask.traceParams": "Arguments",

  /* ── API / 工具层兜底文案 ─────────────────────────────────────────── */
  "api.symbolIncomplete": "The backend returned incomplete symbol data (node missing)",
  "askApi.failed": "Q&A failed (the backend gave no reason)",
  "askApi.noBody": "The backend returned no event stream (response body missing)",
  "events.connectionLost": "Indexing task connection lost",
  "shiki.timeout": "Highlighting timed out ({ms}ms)",
} as const;

/** 所有文案键；`t()` 只接受这些键（写错键名会在编译期报错）。 */
export type MessageKey = keyof typeof en;
