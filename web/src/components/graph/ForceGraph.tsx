import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import Sigma from "sigma";
import { useEffect, useRef } from "react";

import { communityColor, hopColor, kindStroke, nodeRadius } from "../../lib/graphColors";
import type { ForceRendererProps } from "./renderers";

/** 小图（≤400 节点）跑满 200 次迭代；越大越少，交互流畅优先。 */
const LAYOUT_ITERATIONS = 200;
const LAYOUT_MEDIUM_ORDER = 400;
const LAYOUT_LARGE_ORDER = 1200;
/**
 * 打开 Barnes-Hut 近似的节点数阈值。
 * `forceAtlas2.inferSettings` 默认只在 >2000 节点时打开，而默认 limit 是 1500 —— 那时是 O(n²)
 * （1500² × 200 次迭代 ≈ 4.5 亿次浮点运算），足以把主线程堵死好几秒，因此这里把阈值降到 300。
 */
const BARNES_HUT_ORDER = 300;

function layoutIterations(order: number): number {
  if (order <= LAYOUT_MEDIUM_ORDER) return LAYOUT_ITERATIONS;
  if (order <= LAYOUT_LARGE_ORDER) return 120;
  return 60;
}

/**
 * 力导向渲染器（graphology + sigma）。
 *
 * 关键取舍：
 *  - forceatlas2 用同步的 `assign`（不开 webworker）：少一个 worker 生命周期；
 *    通过降低迭代次数 + 打开 Barnes-Hut 把大图的开销压住，超量由上层提示性能风险。
 *  - 初始坐标用「黄金角螺旋」预置：FA2 需要初始坐标，随机坐标会让同一份数据
 *    每次布局结果都不同（不利于对照与回归）。
 *  - **选中态不重建 Sigma**：通过 settings 里的 node/edge reducer 读取 ref 上的当前选中，
 *    选中变化只 `refresh()`，避免相机位置与布局被重置。
 *  - 卸载时 `kill()` 并断开 ResizeObserver，避免 WebGL 上下文泄漏。
 */
export default function ForceGraph({
  nodes,
  edges,
  selectedId,
  onSelectNode,
  hops,
}: ForceRendererProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const rendererRef = useRef<Sigma | null>(null);
  const graphRef = useRef<Graph | null>(null);

  // 回调与选中态放 ref：渲染器只在数据变化时重建，事件/高亮都不触发重建。
  const selectRef = useRef(onSelectNode);
  selectRef.current = onSelectNode;
  const selectedRef = useRef<string | null>(selectedId);
  selectedRef.current = selectedId;

  const hopsRef = useRef(hops);
  hopsRef.current = hops;

  useEffect(() => {
    const container = containerRef.current;
    if (!container || nodes.length === 0) return;

    const graph = new Graph({ multi: false, type: "directed" });
    nodes.forEach((node, index) => {
      const angle = index * 2.399963229728653;
      const radius = Math.sqrt(index + 1) * 3;
      const hopLookup = hopsRef.current;
      const hop = hopLookup?.get(node.id);
      graph.addNode(node.id, {
        x: Math.cos(angle) * radius,
        y: Math.sin(angle) * radius,
        size: nodeRadius(node.degree),
        label: node.name,
        color: typeof hop === "number" ? hopColor(hop) : communityColor(node.community),
        borderColor: kindStroke(node.kind),
        borderSize: 0.16,
        zIndex: 1,
      });
    });

    for (const edge of edges) {
      if (edge.source === edge.target) continue;
      if (!graph.hasNode(edge.source) || !graph.hasNode(edge.target)) continue;
      // sigma 的社区视图里同一对节点只保留一条边（多重边会糊成一团）；
      // 需要看清每一条关系时切到分层 DAG（那边用 multigraph 全画）。
      if (graph.hasEdge(edge.source, edge.target)) continue;
      graph.addEdge(edge.source, edge.target, { size: 0.6, color: "#3f3f46" });
    }

    try {
      const order = graph.order;
      forceAtlas2.assign(graph, {
        iterations: layoutIterations(order),
        settings: {
          ...forceAtlas2.inferSettings(graph),
          gravity: 1,
          barnesHutOptimize: order > BARNES_HUT_ORDER,
        },
      });
    } catch {
      // 布局失败也要能画（只是坐标不好看），不能因为布局异常白屏。
    }

    const renderer = new Sigma(graph, container, {
      renderEdgeLabels: false,
      allowInvalidContainer: true,
      labelDensity: 0.08,
      labelGridCellSize: 90,
      minCameraRatio: 0.05,
      maxCameraRatio: 8,
      nodeReducer: (node: string, data: Record<string, unknown>) => {
        const selected = selectedRef.current;
        if (!selected) return data;
        if (node === selected) {
          const size = typeof data.size === "number" ? data.size : 4;
          return { ...data, size: size * 1.7, borderSize: 0.4, zIndex: 3 };
        }
        return { ...data, color: "#3f3f46", label: "" };
      },
      edgeReducer: (edge: string, data: Record<string, unknown>) => {
        const selected = selectedRef.current;
        if (!selected || !graphRef.current) return data;
        const adjacent = graphRef.current.hasExtremity(edge, selected);
        return adjacent
          ? { ...data, color: "#e4e4e7", size: 1.1 }
          : { ...data, color: "#27272a" };
      },
    });

    renderer.on("clickNode", ({ node }: { node: string }) => selectRef.current(node));
    renderer.on("clickStage", () => selectRef.current(null));

    rendererRef.current = renderer;
    graphRef.current = graph;

    // 容器尺寸变化（侧栏折叠、窗口缩放）时重算相机与画布尺寸。
    let observer: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(() => renderer.refresh());
      observer.observe(container);
    }

    return () => {
      observer?.disconnect();
      renderer.kill();
      rendererRef.current = null;
      graphRef.current = null;
    };
  }, [nodes, edges, hops]);

  // 选中态变化：只刷新，不重建（相机与布局保持不变）。
  useEffect(() => {
    rendererRef.current?.refresh();
  }, [selectedId]);

  return (
    <div
      data-testid="force-canvas"
      ref={containerRef}
      className="h-full w-full"
      aria-label="力导向图谱（sigma 渲染）"
    />
  );
}
