/** 后端 API 客户端：健康检查 + M1 的仓库索引接口。 */

export type Health = {
  status: string;
  version: string;
  home: string;
  llm_configured: boolean;
  web_built: boolean;
};

/** 仓库来源：本地路径或 git 地址。 */
export type RepoSource = "local" | "git";

/** 索引任务状态机的状态。 */
export type JobState = "queued" | "running" | "done" | "error";

/** 索引任务的阶段（进度条中文标签见 JobProgress 组件）。 */
export type JobPhase = "resolve" | "clone" | "walk" | "done";

/** GET /api/repos 与 GET /api/repos/{id} 返回的仓库摘要。 */
export type RepoSummary = {
  repoId: string;
  target: string;
  source: RepoSource;
  ref: string | null;
  rootPath: string;
  state: JobState;
  fileCount: number;
  loc: number;
  languages: Record<string, number>;
  indexedAt: string | null;
  message: string | null;
  jobId: string | null;
};

/** GET /api/jobs/{jobId} 返回的任务状态（轮询用；实时进度走 SSE）。 */
export type JobStatus = {
  id: string;
  repoId: string;
  phase: JobPhase;
  state: JobState;
  progress: number;
  message: string | null;
  startedAt: string | null;
  finishedAt: string | null;
  error: string | null;
};

/** POST /api/repos 的入参。 */
export type RepoCreateBody = {
  target: string;
  ref?: string | null;
  subdir?: string | null;
};

/** POST /api/repos 的返回值。 */
export type RepoCreateResult = {
  repoId: string;
  jobId: string;
};

/**
 * 带 HTTP 状态码的后端错误。
 * message 优先取后端 `detail`，保证 UI 能直接展示可读原因（如「路径不存在」）。
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** 解析后端的错误体：`{"detail": string}`；非 JSON 或字段缺失时退化为「方法 + 路径 + 状态码」。 */
async function errorMessage(res: Response, url: string, method: string): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: unknown } | null;
    const detail = data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  } catch {
    // 后端没返回 JSON（如 502 网关页），忽略并退化为状态码文案。
  }
  return `${method} ${url} → HTTP ${res.status}`;
}

/** 统一的 JSON 请求：非 2xx 一律抛 ApiError，detail 原样带给 UI。 */
export async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const method = init?.method ?? "GET";
  const res = await fetch(url, init);
  if (!res.ok) throw new ApiError(await errorMessage(res, url, method), res.status);
  return (await res.json()) as T;
}

/** GET /api/repos/{id}/files 返回的单个文件条目。 */
export type RepoFile = {
  path: string;
  language: string;
  size: number;
  /** 代码行数（后端按语言规则统计）。 */
  loc: number;
  /** CST 节点总数（解析失败时为 0）。 */
  nodeCount: number;
  parseOk: boolean;
  error: string | null;
};

/** GET /api/repos/{id}/files 的响应：total 是过滤后的总数，可能大于本页条数。 */
export type FileList = {
  total: number;
  files: RepoFile[];
};

/** GET /api/repos/{id}/file 返回的源码文本。 */
export type FileText = {
  path: string;
  language: string;
  size: number;
  lines: number;
  text: string;
  /** 后端为保护内存截断了内容（超大/二进制文件）。 */
  truncated: boolean;
};

/**
 * tree-sitter CST 的一个节点。
 * start/end 为 `[行, 列]`，均 0 起；**列按 UTF-8 字节计**（tree-sitter 约定）。
 */
export type CstNode = {
  type: string;
  /** false 表示匿名节点（标点、关键字字面量等）。 */
  named: boolean;
  /** 在父节点中的字段名（如 `name` / `body`），无字段为 null。 */
  field: string | null;
  start: [number, number];
  end: [number, number];
  startByte: number;
  endByte: number;
  childCount: number;
  /** ERROR 节点：源码里有语法错误。 */
  error: boolean;
  /** 缺失节点：语法要求但源码里没有（如缺右括号）。 */
  missing: boolean;
  /** 因 depth 限制未返回子节点，展开时需要再请求。 */
  truncated: boolean;
  /** 仅叶子节点带源码文本（已截断）。 */
  text: string | null;
  children: CstNode[];
};

/** GET /api/repos/{id}/cst 的响应：node 是 nodePath 指向的子树（深度受 depth 限制）。 */
export type CstResponse = {
  path: string;
  language: string;
  nodePath: string;
  depth: number;
  /** 整棵语法树的节点总数（不是本次返回的节点数）。 */
  totalNodes: number;
  node: CstNode;
};

/** listFiles 的可选参数。 */
export type ListFilesParams = {
  q?: string;
  limit?: number;
  offset?: number;
};

/** 文件列表默认分页大小，与后端默认值保持一致。 */
export const FILE_PAGE_SIZE = 300;

/** 无响应体的请求（如 DELETE 204）。 */
async function requestVoid(url: string, init?: RequestInit): Promise<void> {
  const method = init?.method ?? "GET";
  const res = await fetch(url, init);
  if (!res.ok) throw new ApiError(await errorMessage(res, url, method), res.status);
}

export function getHealth(signal?: AbortSignal): Promise<Health> {
  return requestJson<Health>("/api/health", { signal });
}

/** 提交一个仓库做索引：201 → { repoId, jobId }；400/409 → ApiError(detail)。 */
export function createRepo(body: RepoCreateBody, signal?: AbortSignal): Promise<RepoCreateResult> {
  return requestJson<RepoCreateResult>("/api/repos", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
}

/** 已索引仓库列表（按后端顺序返回）。 */
export async function listRepos(signal?: AbortSignal): Promise<RepoSummary[]> {
  const data = await requestJson<{ repos?: RepoSummary[] }>("/api/repos", { signal });
  return data.repos ?? [];
}

export function getRepo(repoId: string, signal?: AbortSignal): Promise<RepoSummary> {
  return requestJson<RepoSummary>(`/api/repos/${encodeURIComponent(repoId)}`, { signal });
}

export function deleteRepo(repoId: string, signal?: AbortSignal): Promise<void> {
  return requestVoid(`/api/repos/${encodeURIComponent(repoId)}`, {
    method: "DELETE",
    signal,
  });
}

export function getJob(jobId: string, signal?: AbortSignal): Promise<JobStatus> {
  return requestJson<JobStatus>(`/api/jobs/${encodeURIComponent(jobId)}`, { signal });
}

/**
 * 仓库文件列表（M2）：后端做过滤与分页，`q` 为空时不传该参数。
 * 字段缺失按空列表/0 处理，避免旧后端让视图整块崩掉。
 */
export async function listFiles(
  repoId: string,
  params: ListFilesParams = {},
  signal?: AbortSignal,
): Promise<FileList> {
  const search = new URLSearchParams();
  const q = params.q?.trim();
  if (q) search.set("q", q);
  search.set("limit", String(params.limit ?? FILE_PAGE_SIZE));
  search.set("offset", String(params.offset ?? 0));

  const data = await requestJson<{ total?: number; files?: RepoFile[] }>(
    `/api/repos/${encodeURIComponent(repoId)}/files?${search.toString()}`,
    { signal },
  );
  const files = data.files ?? [];
  return { total: typeof data.total === "number" ? data.total : files.length, files };
}

/** 读取单文件源码：404（不存在/越界）会抛 ApiError(status=404)。 */
export function getFileText(repoId: string, path: string, signal?: AbortSignal): Promise<FileText> {
  const search = new URLSearchParams({ path });
  return requestJson<FileText>(
    `/api/repos/${encodeURIComponent(repoId)}/file?${search.toString()}`,
    { signal },
  );
}

/**
 * 读取某节点下的 CST 子树。
 * nodePath 语义：根节点为 `""`，节点 P 的第 i 个子节点为 `P === "" ? String(i) : P + "." + i`。
 * 415（该语言无可用语法）/ 404（文件不存在）会抛 ApiError。
 */
export function getCst(
  repoId: string,
  path: string,
  nodePath: string,
  depth: number,
  signal?: AbortSignal,
): Promise<CstResponse> {
  const search = new URLSearchParams({ path, nodePath, depth: String(depth) });
  return requestJson<CstResponse>(
    `/api/repos/${encodeURIComponent(repoId)}/cst?${search.toString()}`,
    { signal },
  );
}

/* -------------------------------------------------------------------------- */
/* M3：目录树（tree）                                                          */
/* -------------------------------------------------------------------------- */

/** GET /api/repos/{id}/tree 的节点：目录带 children（受 depth 限制），文件 children 恒为 []。 */
export type TreeNode = {
  name: string;
  path: string;
  type: "dir" | "file";
  /** 文件自身行数；目录为其子树之和。 */
  loc: number;
  /** 文件恒为 1；目录为其子树里的文件数。 */
  files: number;
  /** 符号数（后端从图谱聚合；未索引时为 0）。 */
  symbols: number;
  /** 文件语言；目录为 null。 */
  language: string | null;
  errorCount: number;
  children: TreeNode[];
};

/**
 * GET /api/repos/{id}/tree 的响应。
 * 契约上 `node` 一定存在；这里允许 null 只是为了对畸形/空响应兜底（UI 据此走错误态而不是崩掉）。
 */
export type TreeResponse = {
  path: string;
  node: TreeNode | null;
  totalLoc: number;
  totalFiles: number;
};

/** 数字兜底：后端字段缺失/类型不对时退回 fallback，避免 NaN 渗进 UI。 */
function numberOr(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

/** 把后端的节点树补齐成 UI 可信的结构（children 一定是数组、数字一定有限）。 */
function normalizeTreeNode(raw: unknown, fallbackPath = ""): TreeNode | null {
  if (!raw || typeof raw !== "object") return null;
  const source = raw as Partial<TreeNode>;
  const path = typeof source.path === "string" ? source.path : fallbackPath;
  const name =
    typeof source.name === "string" && source.name
      ? source.name
      : path.split("/").filter(Boolean).pop() || path;
  const children = (Array.isArray(source.children) ? source.children : [])
    .map((child) => normalizeTreeNode(child, path))
    .filter((child): child is TreeNode => child !== null);

  return {
    name,
    path,
    type: source.type === "file" ? "file" : "dir",
    loc: numberOr(source.loc, 0),
    files: numberOr(source.files, 0),
    symbols: numberOr(source.symbols, 0),
    language: typeof source.language === "string" ? source.language : null,
    errorCount: numberOr(source.errorCount, 0),
    children,
  };
}

/**
 * 读取目录树：`path` 为空表示仓库根，`depth` 控制一次返回几层（目录才继续展开）。
 * 展开超过 depth 的目录时用该目录路径再请求一次即可（见 DirTreeView）。
 */
export async function getTree(
  repoId: string,
  path = "",
  depth = 3,
  signal?: AbortSignal,
): Promise<TreeResponse> {
  const search = new URLSearchParams({ path, depth: String(depth) });
  const raw = await requestJson<{
    path?: string;
    node?: unknown;
    totalLoc?: unknown;
    totalFiles?: unknown;
  }>(`/api/repos/${encodeURIComponent(repoId)}/tree?${search.toString()}`, { signal });
  const node = normalizeTreeNode(raw?.node, path);
  return {
    path: typeof raw?.path === "string" ? raw.path : path,
    node,
    totalLoc: numberOr(raw?.totalLoc, node?.loc ?? 0),
    totalFiles: numberOr(raw?.totalFiles, node?.files ?? 0),
  };
}

/* -------------------------------------------------------------------------- */
/* M3：知识图谱（analysis / graph / symbol / search）                          */
/* -------------------------------------------------------------------------- */

/** 图谱里的一个符号节点。 */
export type GraphNode = {
  id: string;
  kind: string;
  name: string;
  /** 带作用域的完整名（如 `mod.Class.method`）。 */
  qualified: string;
  file: string | null;
  language: string | null;
  community: number | null;
  degree: number;
  inDegree: number;
  outDegree: number;
  /** 定义位置 `[行, 列]`（0 起）。 */
  start: [number, number] | null;
};

/** 图谱里的一条边；confidence 为 extracted / inferred / ambiguous。 */
export type GraphEdge = {
  source: string;
  target: string;
  relation: string;
  confidence: string;
  file: string | null;
  start: [number, number] | null;
};

/** 图规模统计：total 是「满足过滤条件的全量」，可能大于本次返回的条数。 */
export type GraphTotals = { nodes: number; edges: number };

/** 图查询（/graph、/graph/neighbors）的统一响应。 */
export type GraphResponse = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  total: GraphTotals;
  /** true 表示还有更多节点没返回（UI 显示「显示更多」）。 */
  truncated: boolean;
};

/** 社区（/analysis）。 */
export type CommunitySymbol = { id: string; name: string; kind: string; file: string };

export type Community = {
  id: number;
  name: string;
  size: number;
  cohesion: number | null;
  namedBy: "llm" | "heuristic" | null;
  topSymbols: CommunitySymbol[];
};

/** 高连接度节点（/analysis.godNodes）。 */
export type GodNode = {
  id: string;
  name: string;
  kind: string;
  file: string;
  degree: number;
  inDegree: number;
  outDegree: number;
};

export type Cycle = { files: string[]; size: number };
export type Orphan = { path: string; reason: string };

export type GraphStats = {
  nodes: number;
  edges: number;
  byKind: Record<string, number>;
  byRelation: Record<string, number>;
  byConfidence: Record<string, number>;
  resolvedCallRate: number | null;
};

/** GET /api/repos/{id}/analysis 的响应：图上的聚合结论（不进图谱也能先看结论）。 */
export type AnalysisResponse = {
  communities: Community[];
  godNodes: GodNode[];
  cycles: Cycle[];
  orphans: Orphan[];
  stats: GraphStats;
};

/** getGraph 的入参：kinds/relations/confidence 都是白名单（空数组等价于不过滤）。 */
export type GraphParams = {
  kinds?: readonly string[];
  relations?: readonly string[];
  confidence?: readonly string[];
  community?: number | null;
  limit?: number;
  /** 指定后返回该节点的 N 度邻域，而不是全图 top-N。 */
  focus?: string | null;
  depth?: number;
};

export type NeighborsParams = {
  nodeId: string;
  depth?: number;
  direction?: "both" | "out" | "in";
  relations?: readonly string[];
  limit?: number;
};

export type ImpactParams = {
  nodeId: string;
  depth?: number;
  relations?: readonly string[];
};

export type PathParams = { from: string; to: string; maxDepth?: number };

export type SearchParams = { q: string; kind?: string | null; limit?: number };

export type SymbolSearchResult = {
  id: string;
  kind: string;
  name: string;
  qualified: string;
  file: string;
  start: [number, number] | null;
  snippet: string | null;
};

export type SymbolSearchResponse = { results: SymbolSearchResult[]; total: number };

/** 符号卡片里的出入边（/symbol）。 */
export type SymbolEdgeRef = {
  relation: string;
  confidence: string;
  file: string | null;
  start: [number, number] | null;
};

export type SymbolIncoming = SymbolEdgeRef & { source: string; sourceName: string };
export type SymbolOutgoing = SymbolEdgeRef & { target: string; targetName: string };

export type SymbolDefinition = {
  file: string;
  start: [number, number];
  end: [number, number];
  snippet: string | null;
};

export type SymbolDetail = {
  node: GraphNode;
  incoming: SymbolIncoming[];
  outgoing: SymbolOutgoing[];
  community: { id: number; name: string } | null;
  definition: SymbolDefinition | null;
};

/** GET /api/repos/{id}/graph/impact 的响应：下游闭包 + 需要回归的文件清单。 */
export type ImpactResponse = {
  /** 契约上一定存在；允许 null 以对畸形响应兜底。 */
  root: GraphNode | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  files: { path: string; count: number }[];
  truncated: boolean;
};

/** GET /api/repos/{id}/graph/path 的响应。 */
export type GraphPathResponse = {
  found: boolean;
  nodes: GraphNode[];
  edges: GraphEdge[];
};

/** 把白名单数组拼成后端要的逗号分隔形式；空数组返回 null（= 不传该参数）。 */
function joinList(list: readonly string[] | undefined | null): string | null {
  if (!list || list.length === 0) return null;
  return list.join(",");
}

function normalizeGraph(raw: {
  nodes?: unknown;
  edges?: unknown;
  total?: unknown;
  truncated?: unknown;
}): GraphResponse {
  const nodes = Array.isArray(raw?.nodes) ? (raw.nodes as GraphNode[]) : [];
  const edges = Array.isArray(raw?.edges) ? (raw.edges as GraphEdge[]) : [];
  const total = (raw?.total ?? {}) as Partial<GraphTotals>;
  return {
    nodes,
    edges,
    total: {
      nodes: numberOr(total.nodes, nodes.length),
      edges: numberOr(total.edges, edges.length),
    },
    truncated: raw?.truncated === true,
  };
}

/** 全图查询：默认按 degree 取前 limit 个节点，只返回两端都被选中的边。 */
export async function getGraph(
  repoId: string,
  params: GraphParams = {},
  signal?: AbortSignal,
): Promise<GraphResponse> {
  const search = new URLSearchParams();
  const kinds = joinList(params.kinds);
  const relations = joinList(params.relations);
  const confidence = joinList(params.confidence);
  if (kinds) search.set("kinds", kinds);
  if (relations) search.set("relations", relations);
  if (confidence) search.set("confidence", confidence);
  if (typeof params.community === "number") search.set("community", String(params.community));
  search.set("limit", String(params.limit ?? 1500));
  if (params.focus) {
    search.set("focus", params.focus);
    search.set("depth", String(params.depth ?? 2));
  }
  const raw = await requestJson<Parameters<typeof normalizeGraph>[0]>(
    `/api/repos/${encodeURIComponent(repoId)}/graph?${search.toString()}`,
    { signal },
  );
  return normalizeGraph(raw);
}

/** 某节点的 N 度邻域（分层 DAG 视图的数据源）。 */
export async function getNeighbors(
  repoId: string,
  params: NeighborsParams,
  signal?: AbortSignal,
): Promise<GraphResponse> {
  const search = new URLSearchParams();
  search.set("nodeId", params.nodeId);
  search.set("depth", String(params.depth ?? 2));
  search.set("direction", params.direction ?? "both");
  const relations = joinList(params.relations);
  if (relations) search.set("relations", relations);
  search.set("limit", String(params.limit ?? 400));
  const raw = await requestJson<Parameters<typeof normalizeGraph>[0]>(
    `/api/repos/${encodeURIComponent(repoId)}/graph/neighbors?${search.toString()}`,
    { signal },
  );
  return normalizeGraph(raw);
}

/** 两个符号之间的最短路径（当前 UI 未画路径线，接口先行）。 */
export async function getPath(
  repoId: string,
  params: PathParams,
  signal?: AbortSignal,
): Promise<GraphPathResponse> {
  const search = new URLSearchParams();
  search.set("from", params.from);
  search.set("to", params.to);
  search.set("maxDepth", String(params.maxDepth ?? 6));
  const raw = await requestJson<{ found?: unknown; nodes?: unknown; edges?: unknown }>(
    `/api/repos/${encodeURIComponent(repoId)}/graph/path?${search.toString()}`,
    { signal },
  );
  return {
    found: raw?.found === true,
    nodes: Array.isArray(raw?.nodes) ? (raw.nodes as GraphNode[]) : [],
    edges: Array.isArray(raw?.edges) ? (raw.edges as GraphEdge[]) : [],
  };
}

/** 影响面：某节点的下游闭包 + 按命中次数排序的文件清单。 */
export async function getImpact(
  repoId: string,
  params: ImpactParams,
  signal?: AbortSignal,
): Promise<ImpactResponse> {
  const search = new URLSearchParams();
  search.set("nodeId", params.nodeId);
  search.set("depth", String(params.depth ?? 3));
  const relations = joinList(params.relations);
  if (relations) search.set("relations", relations);
  const raw = await requestJson<{
    root?: unknown;
    nodes?: unknown;
    edges?: unknown;
    files?: unknown;
    truncated?: unknown;
  }>(`/api/repos/${encodeURIComponent(repoId)}/graph/impact?${search.toString()}`, { signal });
  const graph = normalizeGraph(raw);
  return {
    root: (raw?.root as GraphNode | undefined) ?? null,
    nodes: graph.nodes,
    edges: graph.edges,
    files: Array.isArray(raw?.files) ? (raw.files as ImpactResponse["files"]) : [],
    truncated: graph.truncated,
  };
}

/** 符号搜索（图谱搜索框）。 */
export async function searchSymbols(
  repoId: string,
  params: SearchParams,
  signal?: AbortSignal,
): Promise<SymbolSearchResponse> {
  const search = new URLSearchParams();
  const q = params.q.trim();
  if (q) search.set("q", q);
  if (params.kind) search.set("kind", params.kind);
  search.set("limit", String(params.limit ?? 50));
  const raw = await requestJson<{ results?: unknown; total?: unknown }>(
    `/api/repos/${encodeURIComponent(repoId)}/search?${search.toString()}`,
    { signal },
  );
  const results = Array.isArray(raw?.results) ? (raw.results as SymbolSearchResult[]) : [];
  return { results, total: numberOr(raw?.total, results.length) };
}

/** 符号卡片数据：节点本身 + 出入边 + 所属社区 + 定义片段。 */
export async function getSymbol(
  repoId: string,
  nodeId: string,
  signal?: AbortSignal,
): Promise<SymbolDetail> {
  const search = new URLSearchParams({ nodeId });
  const raw = await requestJson<{
    node?: unknown;
    incoming?: unknown;
    outgoing?: unknown;
    community?: unknown;
    definition?: unknown;
  }>(`/api/repos/${encodeURIComponent(repoId)}/symbol?${search.toString()}`, { signal });
  if (!raw?.node || typeof raw.node !== "object") {
    throw new ApiError("后端返回的符号数据不完整（缺少 node）", 502);
  }
  return {
    node: raw.node as GraphNode,
    incoming: Array.isArray(raw.incoming) ? (raw.incoming as SymbolIncoming[]) : [],
    outgoing: Array.isArray(raw.outgoing) ? (raw.outgoing as SymbolOutgoing[]) : [],
    community: (raw.community as SymbolDetail["community"] | undefined) ?? null,
    definition: (raw.definition as SymbolDefinition | undefined) ?? null,
  };
}

/** 图聚合结论（社区 / god nodes / 环 / 孤儿 / 统计）。 */
export async function getAnalysis(repoId: string, signal?: AbortSignal): Promise<AnalysisResponse> {
  const raw = await requestJson<Partial<Record<keyof AnalysisResponse, unknown>>>(
    `/api/repos/${encodeURIComponent(repoId)}/analysis`,
    { signal },
  );
  const stats = (raw?.stats ?? {}) as Partial<GraphStats>;
  return {
    communities: Array.isArray(raw?.communities) ? (raw.communities as Community[]) : [],
    godNodes: Array.isArray(raw?.godNodes) ? (raw.godNodes as GodNode[]) : [],
    cycles: Array.isArray(raw?.cycles) ? (raw.cycles as Cycle[]) : [],
    orphans: Array.isArray(raw?.orphans) ? (raw.orphans as Orphan[]) : [],
    stats: {
      nodes: numberOr(stats.nodes, 0),
      edges: numberOr(stats.edges, 0),
      byKind: stats.byKind ?? {},
      byRelation: stats.byRelation ?? {},
      byConfidence: stats.byConfidence ?? {},
      resolvedCallRate:
        typeof stats.resolvedCallRate === "number" ? stats.resolvedCallRate : null,
    },
  };
}
