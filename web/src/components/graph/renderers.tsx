/**
 * 渲染器抽象层：把「图谱数据」与「具体渲染实现」解耦。
 *
 * 为什么这么做（而不是在测试里 mock `sigma` / `@xyflow/react` 模块）：
 *  - sigma 需要 WebGL/canvas、react-flow 需要布局与 ResizeObserver，两者在 jsdom 里都跑不起来，
 *    mock 模块只能验证「我们调用了它」，一改内部实现测试就假通过；
 *  - 这里把渲染层收敛成**唯一入口** `GraphCanvas`：它只吃
 *    `{nodes, edges, selectedId, onSelectNode}` 这类纯数据，测试用
 *    `setGraphRenderers({ force: Spy })` 注入替身，就能断言「数据确实进了渲染器」，
 *    并且 `sigma` / `react-flow` 的模块在测试里**根本不会被 import**（真实实现是 lazy 的）。
 *  - 生产环境不注册任何替身，走 lazy import 的真实实现（`ForceGraph` / `LayeredDag`）。
 */

import { lazy, Suspense, useSyncExternalStore, type ComponentType } from "react";

import type { GraphEdge, GraphNode } from "../../api/client";
import { useT } from "../../i18n";

/** 力导向渲染器的输入：纯数据 + 两个回调，没有任何外部依赖假设。 */
export type ForceRendererProps = {
  nodes: readonly GraphNode[];
  edges: readonly GraphEdge[];
  selectedId: string | null;
  onSelectNode: (id: string | null) => void;
  /** 影响面模式：节点 id → 到根节点的跳数（用于按跳数着色）。 */
  hops?: ReadonlyMap<string, number>;
};

/** 分层 DAG 的方向：只看下游 / 只看上游 / 双向。 */
export type DagDirection = "both" | "out" | "in";

export type DagRendererProps = {
  nodes: readonly GraphNode[];
  edges: readonly GraphEdge[];
  selectedId: string | null;
  onSelectNode: (id: string | null) => void;
  direction: DagDirection;
};

export type ForceRenderer = ComponentType<ForceRendererProps>;
export type DagRenderer = ComponentType<DagRendererProps>;

export type GraphMode = "force" | "dag" | "impact";

let forceOverride: ForceRenderer | null = null;
let dagOverride: DagRenderer | null = null;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** 注册替身渲染器（测试用）；传 null 表示恢复真实实现。 */
export function setGraphRenderers(next: {
  force?: ForceRenderer | null;
  dag?: DagRenderer | null;
}): void {
  if (next.force !== undefined) forceOverride = next.force;
  if (next.dag !== undefined) dagOverride = next.dag;
  emit();
}

/** 清空替身，回到真实实现（afterEach 里调用，避免测试互相污染）。 */
export function resetGraphRenderers(): void {
  forceOverride = null;
  dagOverride = null;
  emit();
}

const RealForceRenderer = lazy(() => import("./ForceGraph"));
const RealDagRenderer = lazy(() => import("./LayeredDag"));

function useForceRenderer(): ForceRenderer {
  const override = useSyncExternalStore(
    subscribe,
    () => forceOverride,
    () => forceOverride,
  );
  return override ?? (RealForceRenderer as unknown as ForceRenderer);
}

function useDagRenderer(): DagRenderer {
  const override = useSyncExternalStore(
    subscribe,
    () => dagOverride,
    () => dagOverride,
  );
  return override ?? (RealDagRenderer as unknown as DagRenderer);
}

type GraphCanvasProps = ForceRendererProps &
  Pick<DagRendererProps, "direction"> & {
    mode: GraphMode;
  };

/**
 * 唯一的画布入口：按模式挑渲染器。力导向与影响面共用 sigma 渲染器
 * （影响面只是多传一份跳数映射用于着色），分层 DAG 用 react-flow。
 */
export function GraphCanvas({ mode, nodes, edges, selectedId, onSelectNode, hops, direction }: GraphCanvasProps) {
  const t = useT();
  const Force = useForceRenderer();
  const Dag = useDagRenderer();

  return (
    <Suspense
      fallback={
        <div
          data-testid="graph-renderer-loading"
          className="flex h-full items-center justify-center text-xs text-zinc-500"
        >
          {t("graph.rendererLoading")}
        </div>
      }
    >
      {mode === "dag" ? (
        <Dag
          nodes={nodes}
          edges={edges}
          selectedId={selectedId}
          onSelectNode={onSelectNode}
          direction={direction}
        />
      ) : (
        <Force
          nodes={nodes}
          edges={edges}
          selectedId={selectedId}
          onSelectNode={onSelectNode}
          {...(hops ? { hops } : {})}
        />
      )}
    </Suspense>
  );
}
