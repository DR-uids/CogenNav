/** 图谱「焦点 / 选中」相关的纯逻辑：默认焦点、焦点候选列表、影响面跳数。 */

import type { AnalysisResponse, GraphEdge } from "../api/client";

/** 焦点候选（下拉用）。 */
export type FocusOption = {
  id: string;
  name: string;
  kind: string;
  file: string;
};

/**
 * 默认焦点：优先 god nodes 里第一个「结构性」符号（class/function/method），
 * 否则退回 degree 最高的那个。契约里 godNodes 已按 degree 降序，所以直接取首个即可。
 */
export function pickDefaultFocus(godNodes: readonly { id: string; kind: string }[]): string | null {
  if (godNodes.length === 0) return null;
  const structural = godNodes.find(
    (node) => node.kind === "class" || node.kind === "function" || node.kind === "method",
  );
  return (structural ?? godNodes[0])?.id ?? null;
}

/**
 * 焦点候选：god nodes → 各社区的代表符号 → 外部补充（深链/搜索选中的那个）。
 * 去重按 id，顺序保持稳定（下拉不会因为重渲染而跳动）。
 */
export function focusOptions(
  analysis: AnalysisResponse | undefined,
  extra: readonly FocusOption[] = [],
): FocusOption[] {
  const options: FocusOption[] = [];
  const seen = new Set<string>();
  const push = (option: FocusOption) => {
    if (!option.id || seen.has(option.id)) return;
    seen.add(option.id);
    options.push(option);
  };

  for (const node of analysis?.godNodes ?? []) {
    push({ id: node.id, name: node.name, kind: node.kind, file: node.file });
  }
  for (const community of analysis?.communities ?? []) {
    for (const symbol of community.topSymbols ?? []) {
      push({ id: symbol.id, name: symbol.name, kind: symbol.kind, file: symbol.file });
    }
  }
  for (const option of extra) push(option);
  return options;
}

/** 搜索结果 → 焦点候选。 */
export function focusOptionFromSearch(result: {
  id: string;
  name: string;
  kind: string;
  file: string;
}): FocusOption {
  return { id: result.id, name: result.name, kind: result.kind, file: result.file };
}

/**
 * 影响面跳数：从根节点出发沿「源 → 目标」做 BFS，返回 id → 跳数（根为 0）。
 * 只包含能走到的节点（不可达的用默认色，不冒充「未受影响」）。
 */
export function computeHops(
  rootId: string | null | undefined,
  edges: readonly GraphEdge[],
): Map<string, number> {
  const hops = new Map<string, number>();
  if (!rootId) return hops;

  const outgoing = new Map<string, string[]>();
  for (const edge of edges) {
    const list = outgoing.get(edge.source);
    if (list) list.push(edge.target);
    else outgoing.set(edge.source, [edge.target]);
  }

  hops.set(rootId, 0);
  let frontier = [rootId];
  let depth = 0;
  while (frontier.length > 0 && depth < 12) {
    depth += 1;
    const next: string[] = [];
    for (const id of frontier) {
      for (const target of outgoing.get(id) ?? []) {
        if (hops.has(target)) continue;
        hops.set(target, depth);
        next.push(target);
      }
    }
    frontier = next;
  }
  return hops;
}
