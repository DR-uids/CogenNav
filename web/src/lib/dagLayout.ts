/**
 * 分层 DAG 布局：用 `@dagrejs/dagre` 做左到右（rankdir=LR）的分层，纯计算、可单测。
 *
 * 为什么单独抽出来：react-flow 只负责「画 + 交互」，坐标必须由外部算好；
 * 把 rankdir / 节点尺寸 / 去重规则集中在这里，组件里就只剩数据映射。
 */

import dagre from "@dagrejs/dagre";

import type { GraphEdge, GraphNode } from "../api/client";

/** 节点盒子尺寸（与 LayeredDag 里的样式保持一致，否则会重叠/留白）。 */
export const DAG_NODE_WIDTH = 190;
export const DAG_NODE_HEIGHT = 40;

export type DagPosition = { x: number; y: number };

export type DagLayout = {
  /** 节点 id → 左上角坐标（react-flow 的 position 语义）。 */
  positions: Map<string, DagPosition>;
  /** 节点 id → 层号（dagre 的 rank，0 起）。 */
  ranks: Map<string, number>;
  /** 总层数，用于「共 N 层」这类摘要。 */
  rankCount: number;
  width: number;
  height: number;
};

/** 计算分层坐标；节点/边里出现未知 id 时直接跳过（后端截断后可能对不上）。 */
export function layoutDag(
  nodes: readonly GraphNode[],
  edges: readonly GraphEdge[],
): DagLayout {
  const graph = new dagre.graphlib.Graph({ multigraph: true });
  graph.setGraph({ rankdir: "LR", nodesep: 18, ranksep: 90, marginx: 24, marginy: 24 });
  graph.setDefaultEdgeLabel(() => ({}));

  for (const node of nodes) {
    graph.setNode(node.id, { width: DAG_NODE_WIDTH, height: DAG_NODE_HEIGHT });
  }
  for (const edge of edges) {
    if (edge.source === edge.target) continue;
    if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
    // 多重边用「关系 + 端点」命名，同名会互相覆盖。
    graph.setEdge(edge.source, edge.target, {}, `${edge.relation}:${edge.source}->${edge.target}`);
  }

  dagre.layout(graph);

  const positions = new Map<string, DagPosition>();
  for (const node of nodes) {
    const label = graph.node(node.id) as { x?: number; y?: number } | undefined;
    positions.set(node.id, {
      x: (label?.x ?? 0) - DAG_NODE_WIDTH / 2,
      y: (label?.y ?? 0) - DAG_NODE_HEIGHT / 2,
    });
  }

  // 层号不直接用 dagre 的 `rank`：它可能因为 minlen/slack 留出空档（链式三节点会得到 0/2/4），
  // 拿来做「第 N 层」会跳号。这里按实际渲染出的 x 坐标归并成连续的层。
  const columns = [...new Set([...positions.values()].map((position) => Math.round(position.x)))].sort(
    (a, b) => a - b,
  );
  const columnOf = new Map(columns.map((x, index) => [x, index]));
  const ranks = new Map<string, number>();
  for (const [id, position] of positions) {
    ranks.set(id, columnOf.get(Math.round(position.x)) ?? 0);
  }

  const meta = graph.graph() as { width?: number; height?: number } | undefined;
  const maxRank = ranks.size > 0 ? Math.max(...ranks.values()) : 0;

  return {
    positions,
    ranks,
    rankCount: maxRank + 1,
    width: meta?.width ?? 0,
    height: meta?.height ?? 0,
  };
}
