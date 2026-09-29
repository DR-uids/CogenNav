import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import type { SymbolSearchResult } from "../api/client";
import {
  DAG_RELATIONS,
  GRAPH_LARGE_NODES,
  GRAPH_LIMIT_DEFAULT,
  GRAPH_LIMIT_MAX,
  IMPACT_RELATIONS,
  NEIGHBORS_LIMIT_DEFAULT,
  analysisQueryOptions,
  graphQueryOptions,
  impactQueryOptions,
  kindOptions,
  neighborsQueryOptions,
  relationOptions,
} from "../api/graph";
import { GraphFilters } from "../components/graph/GraphFilters";
import { GraphLegend } from "../components/graph/GraphLegend";
import { GraphSearch } from "../components/graph/GraphSearch";
import { ImpactPanel } from "../components/graph/ImpactPanel";
import { SymbolCard } from "../components/graph/SymbolCard";
import { GraphCanvas, type DagDirection, type GraphMode } from "../components/graph/renderers";
import { useT, type MessageKey } from "../i18n";
import { readDeepLinkParam, writeDeepLink } from "../lib/deepLink";
import { confidenceWhitelist, toggleChecked, whitelist } from "../lib/graphFilters";
import { computeHops, focusOptionFromSearch, focusOptions, pickDefaultFocus, type FocusOption } from "../lib/graphSelection";
import { useUi } from "../stores/ui";

const MODE_KEYS: Record<GraphMode, MessageKey> = {
  force: "graphMode.force",
  dag: "graphMode.dag",
  impact: "graphMode.impact",
};

const DIRECTION_KEYS: Record<DagDirection, MessageKey> = {
  both: "graphDirection.both",
  out: "graphDirection.out",
  in: "graphDirection.in",
};

/** 深链 ?mode=；非法值退回默认的力导向。 */
function readMode(): GraphMode {
  const raw = readDeepLinkParam("mode");
  return raw === "dag" || raw === "impact" ? raw : "force";
}

/**
 * 知识图谱视图（M3）：三种模式共用一套过滤/搜索/Inspector，只是数据源与渲染器不同。
 *
 *  - 力导向：`/graph` 全图（degree top-N，可带 focus 取邻域）→ sigma
 *  - 分层 DAG：`/graph/neighbors`（焦点 + 方向）→ react-flow + dagre
 *  - 影响面：`/graph/impact`（下游闭包 + 需回归文件）→ sigma 按跳数着色 + 侧栏清单
 *
 * 渲染层通过 `GraphCanvas`（components/graph/renderers）抽象，测试注入替身即可断言数据，
 * 真实实现（sigma / react-flow）是 lazy import，jsdom 里根本不会被加载。
 */
export function GraphView() {
  const t = useT();
  const repoId = useUi((s) => s.repoId);
  const selectFile = useUi((s) => s.selectFile);
  const selectNode = useUi((s) => s.selectNode);
  const setActiveView = useUi((s) => s.setActiveView);

  // store 里选中的符号（问答视图的引用点击会写这里）；origin 用来区分 CST 的 nodePath。
  const storedSelection = useUi((s) => s.selectedNode);
  const selectionOrigin = useUi((s) => s.selectionOrigin);
  const selectionNonce = useUi((s) => s.selectionNonce);

  const [mode, setMode] = useState<GraphMode>(() => readMode());
  const [focus, setFocus] = useState<string | null>(() => readDeepLinkParam("focus"));
  const [focusExtra, setFocusExtra] = useState<FocusOption | null>(null);
  // ?node= 是「当前选中的符号」（与 CST 视图的 nodePath 同名，但视图之间互不干扰：
  // 切到 CST 前 openFileInCst 会把它清掉）。
  const [selectedId, setSelectedId] = useState<string | null>(() => readDeepLinkParam("node"));
  const [direction, setDirection] = useState<DagDirection>("both");

  // 过滤条件：空数组 = 隐式全选（= 不传参数）
  const [kinds, setKinds] = useState<string[]>([]);
  const [relations, setRelations] = useState<string[]>([]);
  const [confidence, setConfidence] = useState<string[]>([]);
  const [hideAmbiguous, setHideAmbiguous] = useState(true);
  const [community, setCommunity] = useState<number | null>(null);
  const [limit, setLimit] = useState(GRAPH_LIMIT_DEFAULT);

  const enabled = Boolean(repoId);

  const analysisQuery = useQuery({
    ...analysisQueryOptions(repoId ?? ""),
    enabled,
  });
  const analysis = analysisQuery.data;
  const godNodes = analysis?.godNodes ?? [];

  const availableKinds = useMemo(() => kindOptions(analysis?.stats.byKind), [analysis]);
  const availableRelations = useMemo(() => relationOptions(analysis?.stats.byRelation), [analysis]);

  const graphQuery = useQuery({
    ...graphQueryOptions(repoId ?? "", {
      kinds: whitelist(kinds),
      relations: whitelist(relations),
      confidence: confidenceWhitelist(confidence, hideAmbiguous),
      community,
      limit,
      focus,
      depth: 2,
    }),
    enabled: enabled && mode === "force",
    // 换过滤条件时保留上一份数据：画布不卸载（不闪烁），新数据到达后原地替换。
    placeholderData: keepPreviousData,
  });

  const neighborsQuery = useQuery({
    ...neighborsQueryOptions(repoId ?? "", {
      nodeId: focus ?? "",
      depth: 3,
      direction,
      relations: relations.length > 0 ? relations : [...DAG_RELATIONS],
      limit: NEIGHBORS_LIMIT_DEFAULT,
    }),
    enabled: enabled && mode === "dag" && Boolean(focus),
    placeholderData: keepPreviousData,
  });

  const impactQuery = useQuery({
    ...impactQueryOptions(repoId ?? "", {
      nodeId: focus ?? "",
      depth: 3,
      relations: relations.length > 0 ? relations : [...IMPACT_RELATIONS],
    }),
    enabled: enabled && mode === "impact" && Boolean(focus),
    placeholderData: keepPreviousData,
  });

  // 分层 DAG / 影响面必须有焦点：god nodes 拿到后自动挑一个（godNodes[0] 或首个结构符号）。
  useEffect(() => {
    if (mode === "force" || focus) return;
    const fallback = pickDefaultFocus(godNodes);
    if (fallback) setFocus(fallback);
  }, [mode, focus, godNodes]);

  /**
   * 响应 store 里的「符号选中」：问答视图点引用会 selectNode(id, "graph") + 切视图。
   * 为什么用 origin 而不是猜 id 形状：nodePath（`"0.1.2"`）与符号 id
   * （`"python:src/a.py#f.function"`）是两套命名空间，靠字符串形状判断既脆弱又
   * 容易误判；origin 是写入方显式声明的，且图谱页首次挂载时 /analysis 还没回来，
   * 「是不是已知节点 id」根本无从判断。
   * nonce 让「同一个引用连点两次」也能再次触发（值没变时订阅不会重渲染）。
   */
  useEffect(() => {
    if (selectionOrigin !== "graph" || !storedSelection) return;
    setSelectedId(storedSelection);
    setFocus(storedSelection);
    setFocusExtra(null);
  }, [selectionNonce, selectionOrigin, storedSelection]);

  // 深链回写：?mode=（默认值不写）与 ?focus=；首次挂载不回写（URL 本来就是对的）。
  const skipFirstWrite = useRef(true);
  useEffect(() => {
    if (skipFirstWrite.current) {
      skipFirstWrite.current = false;
      return;
    }
    writeDeepLink({ mode: mode === "force" ? null : mode, focus, node: selectedId });
  }, [mode, focus, selectedId]);

  // 换仓库后焦点/选中已无意义，清掉避免请求到不存在的节点。
  const prevRepoId = useRef(repoId);
  useEffect(() => {
    if (prevRepoId.current === repoId) return;
    const hadRepo = prevRepoId.current !== null;
    prevRepoId.current = repoId;
    if (!hadRepo) return;
    setFocus(null);
    setFocusExtra(null);
    setSelectedId(null);
  }, [repoId]);

  const activeNodes =
    mode === "dag"
      ? (neighborsQuery.data?.nodes ?? [])
      : mode === "impact"
        ? (impactQuery.data?.nodes ?? [])
        : (graphQuery.data?.nodes ?? []);
  const activeEdges =
    mode === "dag"
      ? (neighborsQuery.data?.edges ?? [])
      : mode === "impact"
        ? (impactQuery.data?.edges ?? [])
        : (graphQuery.data?.edges ?? []);
  const totalNodes =
    mode === "force"
      ? (graphQuery.data?.total.nodes ?? activeNodes.length)
      : mode === "dag"
        ? (neighborsQuery.data?.total.nodes ?? activeNodes.length)
        : (impactQuery.data?.nodes.length ?? 0);
  const truncated =
    mode === "dag"
      ? Boolean(neighborsQuery.data?.truncated)
      : mode === "impact"
        ? Boolean(impactQuery.data?.truncated)
        : Boolean(graphQuery.data?.truncated);

  const activeQuery =
    mode === "dag" ? neighborsQuery : mode === "impact" ? impactQuery : graphQuery;

  const impactRoot = impactQuery.data?.root ?? null;
  const hops = useMemo(
    () => (mode === "impact" ? computeHops(impactRoot?.id, activeEdges) : undefined),
    [mode, impactRoot, activeEdges],
  );

  const options = useMemo(() => {
    const base = focusOptions(analysis, focusExtra ? [focusExtra] : []);
    // 深链带来的焦点可能不在 /analysis 的候选里（比如刚索引完的私有符号），
    // 补一条，保证下拉框显示的就是当前焦点而不是空。
    if (focus && !base.some((option) => option.id === focus)) {
      base.push({ id: focus, name: focusExtra?.name ?? focus, kind: focusExtra?.kind ?? "symbol", file: focusExtra?.file ?? "" });
    }
    return base;
  }, [analysis, focusExtra, focus]);
  const focusOption = options.find((option) => option.id === focus) ?? null;

  const handlePickSearch = (result: SymbolSearchResult) => {
    setFocus(result.id);
    setFocusExtra(focusOptionFromSearch(result));
    setSelectedId(result.id);
  };

  /** 去 CST 看文件：先清掉 ?node=（否则 CstView 会把符号 id 当成 nodePath 去定位）。 */
  const openFileInCst = (path: string) => {
    writeDeepLink({ node: null });
    selectFile(path);
    selectNode("");
    setActiveView("cst");
  };

  const resetFilters = () => {
    setKinds([]);
    setRelations([]);
    setConfidence([]);
    setHideAmbiguous(true);
    setCommunity(null);
    setLimit(GRAPH_LIMIT_DEFAULT);
  };

  const loading = enabled && activeQuery.isLoading;
  const failed = enabled && activeQuery.isError;
  const needFocus = (mode === "dag" || mode === "impact") && !focus;

  return (
    <div data-testid="view-graph" className="flex h-full min-h-0 bg-zinc-950">
      <div className="flex w-60 shrink-0 flex-col overflow-auto border-r border-zinc-800">
        <GraphFilters
          availableKinds={availableKinds}
          availableRelations={availableRelations}
          kinds={kinds}
          relations={relations}
          confidence={confidence}
          hideAmbiguous={hideAmbiguous}
          onToggleKind={(kind) => setKinds((prev) => toggleChecked(prev, availableKinds, kind))}
          onToggleRelation={(relation) =>
            setRelations((prev) => toggleChecked(prev, availableRelations, relation))
          }
          onToggleConfidence={(value) =>
            setConfidence((prev) =>
              toggleChecked(prev, ["extracted", "inferred", "ambiguous"], value),
            )
          }
          onHideAmbiguousChange={setHideAmbiguous}
          communities={analysis?.communities ?? []}
          community={community}
          onCommunityChange={setCommunity}
          onReset={resetFilters}
          nodeCount={activeNodes.length}
          edgeCount={activeEdges.length}
          notice={
            mode === "force"
              ? undefined
              : t("graphView.notice")
          }
        />
        <GraphLegend
          communities={analysis?.communities ?? []}
          selectedCommunity={community}
          onSelectCommunity={setCommunity}
          impactActive={mode === "impact"}
        />
      </div>

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-zinc-800 px-3 py-2">
          <div className="flex items-center gap-1" role="group" aria-label={t("graphView.modeAria")}>
            {(Object.keys(MODE_KEYS) as GraphMode[]).map((item) => (
              <button
                key={item}
                type="button"
                data-testid={`graph-mode-${item}`}
                aria-pressed={mode === item}
                onClick={() => setMode(item)}
                className={`rounded px-2 py-0.5 text-[11px] ${
                  mode === item
                    ? "bg-zinc-800 text-zinc-100"
                    : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
                }`}
              >
                {t(MODE_KEYS[item])}
              </button>
            ))}
          </div>

          {mode === "dag" && (
            <>
              <div className="flex items-center gap-1" role="group" aria-label={t("graphView.directionAria")}>
                {(Object.keys(DIRECTION_KEYS) as DagDirection[]).map((item) => (
                  <button
                    key={item}
                    type="button"
                    data-testid={`dag-direction-${item}`}
                    aria-pressed={direction === item}
                    onClick={() => setDirection(item)}
                    className={`rounded border border-zinc-800 px-1.5 py-0.5 text-[10px] ${
                      direction === item
                        ? "bg-zinc-800 text-zinc-100"
                        : "text-zinc-400 hover:bg-zinc-900"
                    }`}
                  >
                    {t(DIRECTION_KEYS[item])}
                  </button>
                ))}
              </div>

              <label className="flex items-center gap-1 text-[10px] text-zinc-500">
                {t("graphView.focusNode")}
                <select
                  data-testid="graph-focus-select"
                  value={focus ?? ""}
                  onChange={(event) => setFocus(event.target.value || null)}
                  className="max-w-56 rounded border border-zinc-800 bg-zinc-950 px-1.5 py-0.5 font-mono text-[10px] text-zinc-200 focus:border-zinc-600 focus:outline-none"
                >
                  <option value="">{t("graphView.noSelection")}</option>
                  {options.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.name} · {option.kind}
                    </option>
                  ))}
                </select>
              </label>
            </>
          )}

          <GraphSearch repoId={repoId} onPick={handlePickSearch} />
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-zinc-800 px-3 py-1 text-[10px] text-zinc-500">
          <span data-testid="graph-stats">
            {t("graphView.stats", {
              mode: t(MODE_KEYS[mode]),
              nodes: t("filter.nodes", { count: activeNodes.length }),
              edges: t("filter.edges", { count: activeEdges.length }),
            })}
          </span>
          {focus && (
            <span data-testid="graph-focus" className="flex items-center gap-1">
              {t("graphView.focus")}
              <span className="font-mono text-zinc-300">{focusOption?.name ?? focus}</span>
              {mode === "force" && (
                <button
                  type="button"
                  data-testid="graph-clear-focus"
                  onClick={() => {
                    setFocus(null);
                    setFocusExtra(null);
                  }}
                  className="rounded border border-zinc-800 px-1 hover:bg-zinc-900 hover:text-zinc-200"
                >
                  {t("graphView.backToGlobal")}
                </button>
              )}
            </span>
          )}
          {activeQuery.isFetching && !loading && (
            <span data-testid="graph-refreshing" className="text-sky-400">
              {t("graphView.refreshing")}
            </span>
          )}
          {mode === "dag" && <span>{t("graphView.neighborDepth")}</span>}
          {mode === "impact" && <span>{t("graphView.impactDepth")}</span>}
          {mode === "force" && <span>{t("graphView.limit", { limit })}</span>}
        </div>

        {truncated && (
          <div
            data-testid="graph-truncated"
            className="flex shrink-0 items-center gap-2 border-b border-amber-900/50 bg-amber-950/20 px-3 py-1 text-[10px] text-amber-300"
          >
            <span>{t("graphView.truncated", { shown: activeNodes.length, total: totalNodes })}</span>
            {mode === "force" && (
              <button
                type="button"
                data-testid="graph-show-more"
                onClick={() => setLimit((prev) => Math.min(prev * 2, GRAPH_LIMIT_MAX))}
                className="rounded border border-amber-800 px-1.5 hover:bg-amber-950/40"
              >
                {t("graphView.showMore")}
              </button>
            )}
          </div>
        )}

        {totalNodes > GRAPH_LARGE_NODES && (
          <p
            data-testid="graph-large-warning"
            className="shrink-0 border-b border-amber-900/40 bg-amber-950/10 px-3 py-1 text-[10px] text-amber-400"
          >
            {t("graphView.largeWarning", { limit: GRAPH_LARGE_NODES })}
          </p>
        )}

        <div className="relative min-h-0 flex-1">
          {!repoId && (
            <p data-testid="graph-no-repo" className="m-4 text-xs leading-relaxed text-zinc-500">
              {t("graphView.pickRepo")}
            </p>
          )}

          {needFocus && (
            <p data-testid="graph-need-focus" className="m-4 text-xs leading-relaxed text-zinc-500">
              {t("graphView.needFocus", { mode: t(MODE_KEYS[mode]) })}
            </p>
          )}

          {enabled && !needFocus && loading && (
            <p data-testid="graph-loading" className="m-4 text-xs text-zinc-500">
              {t("graphView.loading")}
            </p>
          )}

          {enabled && !needFocus && !loading && failed && (
            <p
              data-testid="graph-error"
              className="m-4 rounded border border-rose-900/60 bg-rose-950/30 p-3 text-xs break-all text-rose-300"
            >
              {activeQuery.error instanceof Error ? activeQuery.error.message : t("graphView.loadFailed")}
            </p>
          )}

          {enabled && !needFocus && !loading && !failed && activeNodes.length === 0 && (
            <p data-testid="graph-empty" className="m-4 text-xs leading-relaxed text-zinc-500">
              {t("graphView.empty")}
            </p>
          )}

          {enabled && !needFocus && !failed && activeNodes.length > 0 && (
            <GraphCanvas
              mode={mode}
              nodes={activeNodes}
              edges={activeEdges}
              selectedId={selectedId}
              onSelectNode={setSelectedId}
              direction={direction}
              {...(hops ? { hops } : {})}
            />
          )}
        </div>
      </div>

      <aside className="flex w-80 shrink-0 flex-col overflow-auto border-l border-zinc-800 bg-zinc-900/30">
        <div className="flex h-10 shrink-0 items-center justify-between border-b border-zinc-800 px-3">
          <h3 className="text-xs font-medium text-zinc-400">{t("graphView.inspector")}</h3>
          {selectedId && (
            <button
              type="button"
              data-testid="graph-inspector-clear"
              onClick={() => setSelectedId(null)}
              className="rounded border border-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
            >
              {t("graphView.clear")}
            </button>
          )}
        </div>

        {selectedId ? (
          <SymbolCard
            repoId={repoId}
            nodeId={selectedId}
            onSelectNode={setSelectedId}
            onOpenInCst={openFileInCst}
          />
        ) : (
          <p data-testid="graph-inspector-empty" className="p-3 text-[11px] leading-relaxed text-zinc-500">
            {t("graphView.inspectorEmpty")}
          </p>
        )}

        {mode === "impact" && (
          <ImpactPanel
            root={impactRoot}
            files={impactQuery.data?.files ?? []}
            nodeCount={activeNodes.length}
            truncated={truncated}
            onOpenFile={openFileInCst}
          />
        )}
      </aside>
    </div>
  );
}
