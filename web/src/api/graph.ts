/**
 * M3 目录树 / 知识图谱查询的共享定义（与 `api/cst.ts` 同一套模式）。
 *
 * 为什么集中在这里：过滤面板、渲染器、Inspector 需要同一份数据，用同一个 queryKey
 * 谁先请求都不会重复打后端；同时把「参数 → queryKey」的归一化收在一处，
 * 避免数组/对象顺序不同导致缓存命中不了。
 */

import {
  getAnalysis,
  getGraph,
  getImpact,
  getNeighbors,
  getPath,
  getSymbol,
  getTree,
  searchSymbols,
  type AnalysisResponse,
  type GraphParams,
  type ImpactParams,
  type NeighborsParams,
  type PathParams,
  type SearchParams,
  type SymbolSearchResponse,
  type TreeResponse,
} from "./client";

/** 目录树一次取几层；展开更深的目录时按该目录路径再请求一次。 */
export const TREE_DEPTH_DEFAULT = 3;

/** 力导向图默认节点上限（契约默认 1500）。 */
export const GRAPH_LIMIT_DEFAULT = 1500;
/** 「显示更多」每次把上限翻倍，硬顶防止把浏览器拖死。 */
export const GRAPH_LIMIT_MAX = 24000;
/** 邻域查询默认上限（后端默认 400）。 */
export const NEIGHBORS_LIMIT_DEFAULT = 400;
/** 节点数超过它时提示性能风险，但仍允许继续。 */
export const GRAPH_LARGE_NODES = 5000;

/** 影响面的默认关系白名单（与后端默认值一致）。 */
export const IMPACT_RELATIONS = ["calls", "references", "imports"] as const;
/** 分层 DAG 的默认关系白名单：只保留结构性调用关系，图才读得懂。 */
export const DAG_RELATIONS = ["calls", "imports", "extends"] as const;

/** 置信度档位（从高到低）。 */
export const CONFIDENCE_LEVELS = ["extracted", "inferred", "ambiguous"] as const;

/** 后端没返回 byKind 时用的兜底类型清单。 */
export const FALLBACK_KINDS = [
  "class",
  "function",
  "method",
  "interface",
  "type",
  "variable",
  "module",
] as const;

/** 后端没返回 byRelation 时用的兜底关系清单。 */
export const FALLBACK_RELATIONS = [
  "calls",
  "references",
  "imports",
  "extends",
  "implements",
  "contains",
  "instantiates",
] as const;

/** 索引产物在一次索引内不变，因此所有查询都 staleTime=Infinity + 不重试。 */
const STALE_FOREVER = Number.POSITIVE_INFINITY;

function baseOptions<T>(queryKey: readonly unknown[], queryFn: () => Promise<T>) {
  return { queryKey, queryFn, staleTime: STALE_FOREVER, retry: false as const };
}

/** `["tree", repoId, path, depth]`。 */
export function treeQueryKey(
  repoId: string,
  path = "",
  depth = TREE_DEPTH_DEFAULT,
): readonly [string, string, string, number] {
  return ["tree", repoId, path, depth];
}

export function treeQueryOptions(repoId: string, path = "", depth = TREE_DEPTH_DEFAULT) {
  return {
    queryKey: treeQueryKey(repoId, path, depth),
    queryFn: ({ signal }: { signal: AbortSignal }) => getTree(repoId, path, depth, signal),
    staleTime: STALE_FOREVER,
    retry: false as const,
  } satisfies {
    queryKey: readonly unknown[];
    queryFn: (context: { signal: AbortSignal }) => Promise<TreeResponse>;
    staleTime: number;
    retry: false;
  };
}

export function analysisQueryKey(repoId: string): readonly [string, string] {
  return ["analysis", repoId];
}

export function analysisQueryOptions(repoId: string) {
  return baseOptions<AnalysisResponse>(analysisQueryKey(repoId), () => getAnalysis(repoId));
}

/**
 * 过滤参数的稳定序列化：数组顺序固定、可选字段缺省即省略。
 * 任一参数变化都会换 queryKey，从而重新请求（过滤面板就是靠这个生效的）。
 */
export function graphParamsKey(params: GraphParams): string {
  const parts: string[] = [
    `kinds=${params.kinds?.join(",") ?? ""}`,
    `relations=${params.relations?.join(",") ?? ""}`,
    `confidence=${params.confidence?.join(",") ?? ""}`,
    `community=${typeof params.community === "number" ? params.community : ""}`,
    `limit=${params.limit ?? GRAPH_LIMIT_DEFAULT}`,
    `focus=${params.focus ?? ""}`,
    `depth=${params.focus ? (params.depth ?? 2) : ""}`,
  ];
  return parts.join(";");
}

export function graphQueryKey(repoId: string, params: GraphParams) {
  return ["graph", repoId, graphParamsKey(params)] as const;
}

export function graphQueryOptions(repoId: string, params: GraphParams) {
  return {
    queryKey: graphQueryKey(repoId, params),
    queryFn: ({ signal }: { signal: AbortSignal }) => getGraph(repoId, params, signal),
    staleTime: STALE_FOREVER,
    retry: false as const,
  };
}

export function neighborsQueryKey(repoId: string, params: NeighborsParams) {
  return [
    "neighbors",
    repoId,
    params.nodeId,
    params.direction ?? "both",
    params.depth ?? 2,
    params.relations?.join(",") ?? "",
    params.limit ?? NEIGHBORS_LIMIT_DEFAULT,
  ] as const;
}

export function neighborsQueryOptions(repoId: string, params: NeighborsParams) {
  return {
    queryKey: neighborsQueryKey(repoId, params),
    queryFn: ({ signal }: { signal: AbortSignal }) => getNeighbors(repoId, params, signal),
    staleTime: STALE_FOREVER,
    retry: false as const,
  };
}

export function impactQueryKey(repoId: string, params: ImpactParams) {
  return [
    "impact",
    repoId,
    params.nodeId,
    params.depth ?? 3,
    params.relations?.join(",") ?? "",
  ] as const;
}

export function impactQueryOptions(repoId: string, params: ImpactParams) {
  return {
    queryKey: impactQueryKey(repoId, params),
    queryFn: ({ signal }: { signal: AbortSignal }) => getImpact(repoId, params, signal),
    staleTime: STALE_FOREVER,
    retry: false as const,
  };
}

export function pathQueryKey(repoId: string, params: PathParams) {
  return ["graphPath", repoId, params.from, params.to, params.maxDepth ?? 6] as const;
}

export function pathQueryOptions(repoId: string, params: PathParams) {
  return {
    queryKey: pathQueryKey(repoId, params),
    queryFn: ({ signal }: { signal: AbortSignal }) => getPath(repoId, params, signal),
    staleTime: STALE_FOREVER,
    retry: false as const,
  };
}

export function symbolQueryKey(repoId: string, nodeId: string) {
  return ["symbol", repoId, nodeId] as const;
}

export function symbolQueryOptions(repoId: string, nodeId: string) {
  return {
    queryKey: symbolQueryKey(repoId, nodeId),
    queryFn: ({ signal }: { signal: AbortSignal }) => getSymbol(repoId, nodeId, signal),
    staleTime: STALE_FOREVER,
    retry: false as const,
  };
}

export function searchQueryKey(repoId: string, q: string, kind: string | null, limit: number) {
  return ["search", repoId, q, kind ?? "", limit] as const;
}

export function searchQueryOptions(
  repoId: string,
  params: SearchParams,
  limit = 50,
) {
  return {
    queryKey: searchQueryKey(repoId, params.q, params.kind ?? null, limit),
    queryFn: ({ signal }: { signal: AbortSignal }) =>
      searchSymbols(repoId, { ...params, limit }, signal),
    staleTime: STALE_FOREVER,
    retry: false as const,
  } satisfies {
    queryKey: readonly unknown[];
    queryFn: (context: { signal: AbortSignal }) => Promise<SymbolSearchResponse>;
    staleTime: number;
    retry: false;
  };
}

/**
 * 过滤面板的可用取值：优先用后端统计里的键（真实出现过的类型/关系），
 * 再并上兜底清单，保证面板永远有可点的项。
 */
export function kindOptions(byKind: Record<string, number> | undefined): string[] {
  const keys = Object.keys(byKind ?? {});
  return keys.length > 0 ? keys : [...FALLBACK_KINDS];
}

export function relationOptions(byRelation: Record<string, number> | undefined): string[] {
  const keys = Object.keys(byRelation ?? {});
  return keys.length > 0 ? keys : [...FALLBACK_RELATIONS];
}
