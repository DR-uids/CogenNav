import { describe, expect, test } from "vitest";

import type { GraphEdge, GraphNode } from "../api/client";
import { DAG_NODE_WIDTH, layoutDag } from "./dagLayout";

function node(id: string): GraphNode {
  return {
    id,
    kind: "function",
    name: id,
    qualified: id,
    file: `src/${id}.ts`,
    language: "typescript",
    community: 0,
    degree: 1,
    inDegree: 0,
    outDegree: 0,
    start: [0, 0],
  };
}

function edge(source: string, target: string, relation = "calls"): GraphEdge {
  return { source, target, relation, confidence: "extracted", file: null, start: null };
}

describe("dagLayout 分层布局", () => {
  test("左到右分层：rank 越大 x 越大，层号从 0 开始", () => {
    const nodes = [node("a"), node("b"), node("c")];
    const layout = layoutDag(nodes, [edge("a", "b"), edge("b", "c")]);

    expect(layout.positions.size).toBe(3);
    const x = (id: string) => layout.positions.get(id)?.x ?? Number.NaN;
    expect(x("a")).toBeLessThan(x("b"));
    expect(x("b")).toBeLessThan(x("c"));

    expect(layout.ranks.get("a")).toBe(0);
    expect(layout.ranks.get("b")).toBe(1);
    expect(layout.ranks.get("c")).toBe(2);
    expect(layout.rankCount).toBe(3);
    expect(layout.width).toBeGreaterThan(0);
    expect(layout.height).toBeGreaterThan(0);
  });

  test("坐标是左上角（已减去节点尺寸的一半，避免 react-flow 里重叠）", () => {
    const layout = layoutDag([node("a"), node("b")], [edge("a", "b")]);
    const a = layout.positions.get("a");
    const b = layout.positions.get("b");
    expect(a).toBeDefined();
    expect(b).toBeDefined();
    // dagre 给的是中心点，同一层内的两个节点不会重合
    expect((b?.x ?? 0) - (a?.x ?? 0)).toBeGreaterThanOrEqual(DAG_NODE_WIDTH);
  });

  test("忽略自环与未知端点；孤立节点也有坐标", () => {
    const layout = layoutDag(
      [node("a"), node("b"), node("lonely")],
      [edge("a", "b"), edge("b", "b"), edge("b", "missing")],
    );
    expect(layout.positions.size).toBe(3);
    expect(layout.positions.get("lonely")).toBeDefined();
    expect(layout.rankCount).toBe(2);
  });

  test("空图不抛异常", () => {
    const layout = layoutDag([], []);
    expect(layout.positions.size).toBe(0);
    expect(layout.rankCount).toBe(1);
  });
});
