import "@xyflow/react/dist/style.css";

import {
  Background,
  Controls,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import { useMemo } from "react";

import { useT, type MessageKey } from "../../i18n";
import { DAG_NODE_HEIGHT, DAG_NODE_WIDTH, layoutDag } from "../../lib/dagLayout";
import { kindStroke } from "../../lib/graphColors";
import type { DagDirection, DagRendererProps } from "./renderers";

const DIRECTION_KEYS: Record<DagDirection, MessageKey> = {
  both: "dagDirection.both",
  out: "dagDirection.out",
  in: "dagDirection.in",
};

/**
 * 分层 DAG 渲染器（@xyflow/react + @dagrejs/dagre）。
 *
 * 数据来自 `/graph/neighbors`（后端已按 direction 过滤），这里只做：
 *  - dagre 左到右分层 → react-flow 节点坐标
 *  - 节点描边按 kind、填充按社区；选中节点高亮
 *  - 点击节点回调给上层（Inspector 用）
 */
export default function LayeredDag({
  nodes,
  edges,
  selectedId,
  onSelectNode,
  direction,
}: DagRendererProps) {
  const t = useT();
  const { rfNodes, rfEdges, ranks, rankCount } = useMemo(() => {
    const layout = layoutDag(nodes, edges);

    const rfNodes: Node[] = nodes.map((node) => ({
      id: node.id,
      position: layout.positions.get(node.id) ?? { x: 0, y: 0 },
      data: { label: node.name },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      selected: node.id === selectedId,
      style: {
        width: DAG_NODE_WIDTH,
        height: DAG_NODE_HEIGHT,
        padding: "4px 8px",
        fontSize: 11,
        fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
        borderRadius: 4,
        background: node.id === selectedId ? "#3f3f46" : "#18181b",
        color: node.id === selectedId ? "#fafafa" : "#d4d4d8",
        border: `1px solid ${node.id === selectedId ? "#fafafa" : kindStroke(node.kind)}`,
      },
    }));

    const rfEdges: Edge[] = edges
      .filter((edge) => edge.source !== edge.target)
      .map((edge, index) => ({
        id: `${edge.source}->${edge.target}:${edge.relation}:${index}`,
        source: edge.source,
        target: edge.target,
        label: edge.relation,
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed, color: "#71717a", width: 14, height: 14 },
        style: {
          stroke: edge.confidence === "ambiguous" ? "#52525b" : "#71717a",
          strokeDasharray: edge.confidence === "ambiguous" ? "4 3" : undefined,
        },
        labelStyle: { fill: "#a1a1aa", fontSize: 9 },
        labelBgStyle: { fill: "#18181b" },
      }));

    return { rfNodes, rfEdges, ranks: layout.ranks, rankCount: layout.rankCount };
  }, [nodes, edges, selectedId]);

  return (
    <div data-testid="dag-canvas" className="relative h-full w-full">
      <ReactFlow
        nodes={rfNodes}
        edges={rfEdges}
        fitView
        minZoom={0.1}
        maxZoom={2}
        nodesDraggable={false}
        nodesConnectable={false}
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_event, node) => onSelectNode(node.id)}
        onPaneClick={() => onSelectNode(null)}
      >
        <Background color="#27272a" gap={18} />
        <Controls showInteractive={false} />
      </ReactFlow>

      <div
        data-testid="dag-summary"
        className="pointer-events-none absolute top-2 left-3 rounded bg-zinc-950/80 px-2 py-0.5 text-[10px] text-zinc-400"
      >
        {t("dag.summary", {
          direction: t(DIRECTION_KEYS[direction]),
          nodes: t("unit.nodes", { count: rfNodes.length }),
          ranks: t("dag.rank", { count: rankCount }),
        })}
        {selectedId && ranks.has(selectedId)
          ? t("dag.focusLayer", { layer: (ranks.get(selectedId) ?? 0) + 1 })
          : ""}
      </div>
    </div>
  );
}
