import { useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";

import { CST_DEPTH, cstQueryKey, cstQueryOptions } from "../../api/cst";
import { ApiError, type CstNode } from "../../api/client";
import { childPath, pathSegments } from "../../lib/cstPath";
import { formatRange } from "../../lib/cstRange";
import { useUi } from "../../stores/ui";

const ROW_HEIGHT = 24;

/** 扁平化后的一行：nodePath 同时是 React key、选中标识与懒展开请求参数。 */
type CstRow = {
  path: string;
  node: CstNode;
  depth: number;
};

const EMPTY_ROWS: CstRow[] = [];

/**
 * 把（可能只加载了一部分的）语法树按「已展开集合」拉平成行数组。
 * 已懒加载的子树以 subNodes 为准，正在请求中的子树先按「没有子节点」渲染，
 * 数据到达后由调用方重新计算（虚拟列表只认扁平数组，这比维护树形组件简单得多）。
 */
function buildRows(
  root: CstNode,
  expanded: ReadonlySet<string>,
  fetchPaths: readonly string[],
  subNodes: ReadonlyMap<string, CstNode>,
): CstRow[] {
  const rows: CstRow[] = [];
  const stack: CstRow[] = [{ path: "", node: root, depth: 0 }];

  while (stack.length > 0) {
    const row = stack.pop();
    if (!row) break;
    rows.push(row);
    if (!expanded.has(row.path)) continue;

    const loaded = subNodes.get(row.path);
    const children = loaded
      ? (loaded.children ?? [])
      : fetchPaths.includes(row.path)
        ? []
        : (row.node.children ?? []);

    for (let i = children.length - 1; i >= 0; i -= 1) {
      stack.push({ path: childPath(row.path, i), node: children[i], depth: row.depth + 1 });
    }
  }

  return rows;
}

function textPreview(text: string): string {
  const flat = text.replace(/\s+/g, " ").trim();
  return flat.length > 40 ? `${flat.slice(0, 40)}…` : flat;
}

function CstNodeRow({
  row,
  selected,
  open,
  pending,
  error,
  onToggle,
  onSelect,
  onRetry,
}: {
  row: CstRow;
  selected: boolean;
  open: boolean;
  pending: boolean;
  error: string | null;
  onToggle: () => void;
  onSelect: () => void;
  onRetry: () => void;
}) {
  const { node } = row;
  const canExpand = node.childCount > 0;
  const typeTone = node.error
    ? "text-rose-400"
    : node.named
      ? "text-zinc-200"
      : "text-zinc-500 italic";

  return (
    <div
      role="treeitem"
      aria-level={row.depth + 1}
      aria-selected={selected}
      aria-expanded={canExpand ? open : undefined}
      aria-label={node.type}
      data-testid="cst-node-row"
      data-nodepath={row.path}
      data-selected={selected ? "true" : "false"}
      data-error={node.error ? "true" : "false"}
      onClick={onSelect}
      className={`absolute top-0 left-0 flex w-full cursor-pointer items-center gap-1.5 overflow-hidden pr-2 text-[11px] leading-6 whitespace-nowrap ${
        selected ? "bg-zinc-800" : "hover:bg-zinc-900/70"
      }`}
      style={{ height: ROW_HEIGHT, paddingLeft: 4 + row.depth * 14 }}
    >
      {canExpand ? (
        <button
          type="button"
          data-testid="cst-node-toggle"
          aria-label={`${open ? "折叠" : "展开"} ${node.type}`}
          onClick={(event) => {
            event.stopPropagation();
            onToggle();
          }}
          className="w-3 shrink-0 text-[9px] text-zinc-500 hover:text-zinc-200"
        >
          {open ? "▼" : "▶"}
        </button>
      ) : (
        <span className="w-3 shrink-0" />
      )}

      <span className={`shrink-0 font-mono ${typeTone}`}>{node.type}</span>

      {node.field && (
        <span
          data-testid="cst-node-field"
          className="shrink-0 rounded bg-sky-950/60 px-1 text-[10px] text-sky-300"
        >
          {node.field}
        </span>
      )}
      {node.error && <span className="shrink-0 text-[10px] text-rose-400">ERROR</span>}
      {node.missing && <span className="shrink-0 text-[10px] text-amber-400">缺失</span>}

      <span data-testid="cst-node-range" className="shrink-0 font-mono text-[10px] text-zinc-600">
        {formatRange({ start: node.start, end: node.end })}
      </span>

      {node.text && (
        <span data-testid="cst-node-text" className="truncate font-mono text-[10px] text-zinc-500">
          {textPreview(node.text)}
        </span>
      )}

      {pending && (
        <span data-testid="cst-node-loading" className="shrink-0 text-[10px] text-zinc-500">
          加载中…
        </span>
      )}

      {error && (
        <span className="flex shrink-0 items-center gap-1 text-[10px] text-rose-400">
          <span data-testid="cst-subtree-error" className="truncate">
            子树加载失败：{error}
          </span>
          <button
            type="button"
            data-testid="cst-subtree-retry"
            onClick={(event) => {
              event.stopPropagation();
              onRetry();
            }}
            className="rounded border border-rose-900/60 px-1 hover:bg-rose-950/40"
          >
            重试
          </button>
        </span>
      )}
    </div>
  );
}

/**
 * 中间面板：CST 树。
 *
 * 虚拟滚动 + 懒展开：首屏只请求 depth=4，`truncated` 的节点被展开时才按
 * `nodePath=<该节点路径>&depth=4` 再请求一层；返回的子树进 React Query 缓存，
 * 与源码面板共用同一个 queryKey，因此不会重复请求。
 */
export function CstTree({ repoId, file }: { repoId: string | null; file: string | null }) {
  const selectedNode = useUi((s) => s.selectedNode);
  const selectNode = useUi((s) => s.selectNode);
  const queryClient = useQueryClient();

  // 根节点默认展开：否则首屏只有一行 "module"，看不到任何结构。
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set<string>([""]));
  const [fetchPaths, setFetchPaths] = useState<readonly string[]>([]);

  const enabled = Boolean(repoId && file);
  const rootQuery = useQuery({
    ...cstQueryOptions(repoId ?? "", file ?? "", "", CST_DEPTH),
    enabled,
  });

  const subQueries = useQueries({
    queries: fetchPaths.map((path) => ({
      ...cstQueryOptions(repoId ?? "", file ?? "", path, CST_DEPTH),
      enabled,
    })),
  });

  const subNodes = new Map<string, CstNode>();
  const subErrors = new Map<string, string>();
  subQueries.forEach((result, index) => {
    const path = fetchPaths[index];
    if (path === undefined) return;
    if (result.data) subNodes.set(path, result.data.node);
    else if (result.error) {
      subErrors.set(path, result.error instanceof Error ? result.error.message : "子树加载失败");
    }
  });
  // 作为 useMemo / useEffect 的依赖：子树数据变化时驱动「重新拉平」与「继续展开祖先链」。
  const subVersion = subQueries.map((r) => `${r.dataUpdatedAt}:${r.errorUpdatedAt}`).join("|");

  const rootNode = rootQuery.data?.node ?? null;

  const rows = useMemo(
    () => (rootNode ? buildRows(rootNode, expanded, fetchPaths, subNodes) : EMPTY_ROWS),
    // subNodes 每次渲染都是新 Map，用 subVersion 代表它的内容变化。
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [rootNode, expanded, fetchPaths, subVersion],
  );

  /** 取某个节点的子节点：已懒加载的优先，请求中则先当作空。 */
  const childrenOf = (path: string, node: CstNode): CstNode[] => {
    const loaded = subNodes.get(path);
    if (loaded) return loaded.children ?? [];
    if (path !== "" && fetchPaths.includes(path)) return [];
    return node.children ?? [];
  };

  // 深链 / 外部选中：逐级展开目标节点的祖先链；遇到未加载的子树就补一次请求，
  // 数据到达后 effect 因 subVersion 变化重跑，继续往下走（收敛：只在有新增时才 setState）。
  useEffect(() => {
    if (!rootNode || selectedNode === null) return;
    const segments = pathSegments(selectedNode);
    if (segments.length === 0) return;

    const toExpand: string[] = [];
    const toFetch: string[] = [];
    let node: CstNode = rootNode;
    let path = "";

    for (const segment of segments) {
      if (!expanded.has(path)) toExpand.push(path);
      if (path !== "" && node.truncated && !fetchPaths.includes(path) && !subNodes.has(path)) {
        toFetch.push(path);
      }
      const next = childrenOf(path, node)[segment];
      if (!next) break;
      node = next;
      path = childPath(path, segment);
    }

    if (toExpand.length > 0) {
      setExpanded((prev) => new Set([...prev, ...toExpand]));
    }
    if (toFetch.length > 0) {
      setFetchPaths((prev) => [...prev, ...toFetch.filter((p) => !prev.includes(p))]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rootNode, selectedNode, expanded, fetchPaths, subVersion]);

  const toggle = (path: string, node: CstNode) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
    if (path !== "" && node.truncated) {
      setFetchPaths((prev) => (prev.includes(path) ? prev : [...prev, path]));
    }
  };

  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 20,
  });
  const virtualItems = virtualizer.getVirtualItems();

  const rootError = rootQuery.error;
  const noGrammar = rootError instanceof ApiError && rootError.status === 415;
  const notFound = rootError instanceof ApiError && rootError.status === 404;

  return (
    <section
      data-testid="cst-tree-panel"
      className="flex min-w-0 flex-1 flex-col border-r border-zinc-800"
    >
      <div className="flex h-10 shrink-0 items-center justify-between gap-2 border-b border-zinc-800 px-3">
        <h3 className="truncate text-xs font-medium text-zinc-400">
          CST 语法树
          {file && <span className="ml-2 font-mono text-[10px] text-zinc-500">{file}</span>}
        </h3>
        <span className="flex shrink-0 items-center gap-2 text-[10px] text-zinc-600">
          {rootQuery.data && (
            <span data-testid="cst-total-nodes">共 {rootQuery.data.totalNodes} 个节点</span>
          )}
          <span>展开深度 {CST_DEPTH}</span>
          {expanded.size > 0 && (
            <button
              type="button"
              data-testid="cst-collapse-all"
              onClick={() => setExpanded(new Set<string>())}
              className="rounded border border-zinc-800 px-1.5 py-0.5 text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
            >
              全部折叠
            </button>
          )}
        </span>
      </div>

      {!file && (
        <p data-testid="cst-tree-empty" className="m-4 text-xs leading-relaxed text-zinc-500">
          从左侧选择一个文件，这里会显示它的 CST 语法树；展开节点可按需下钻。
        </p>
      )}

      {file && rootQuery.isLoading && (
        <p data-testid="cst-loading" className="m-4 text-xs text-zinc-500">
          正在解析语法树…
        </p>
      )}

      {file && noGrammar && (
        <div
          data-testid="cst-no-grammar"
          className="m-4 rounded border border-amber-900/60 bg-amber-950/20 p-3 text-xs leading-relaxed text-amber-300"
        >
          <p className="font-medium">该语言没有可用的语法</p>
          <p className="mt-1 text-amber-200/80">
            后端未提供这种语言的 tree-sitter 解析器，无法展示 CST；右侧源码仍可正常浏览。
          </p>
        </div>
      )}

      {file && !noGrammar && rootError && (
        <p
          data-testid="cst-tree-error"
          className="m-4 rounded border border-rose-900/60 bg-rose-950/30 p-3 text-xs break-all text-rose-300"
        >
          {notFound ? "文件不存在或超出仓库范围：" : "语法树加载失败："}
          {rootError instanceof Error ? rootError.message : "未知错误"}
        </p>
      )}

      {file && rootNode && (
        <div ref={scrollRef} data-testid="cst-tree-scroll" className="min-h-0 flex-1 overflow-auto">
          <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
            {virtualItems.map((item) => {
              const row = rows[item.index];
              if (!row) return null;
              const path = row.path;
              return (
                <div
                  key={path}
                  className="absolute top-0 left-0 w-full"
                  style={{ height: item.size, transform: `translateY(${item.start}px)` }}
                >
                  <CstNodeRow
                    row={row}
                    selected={selectedNode === path}
                    open={expanded.has(path)}
                    pending={
                      path !== "" &&
                      fetchPaths.includes(path) &&
                      !subNodes.has(path) &&
                      !subErrors.has(path)
                    }
                    error={subErrors.get(path) ?? null}
                    onToggle={() => toggle(path, row.node)}
                    onSelect={() => selectNode(path)}
                    onRetry={() => {
                      // 让这一棵子树的请求重新打一次（错误态下 react-query 不会自动重试）。
                      void queryClient.invalidateQueries({
                        queryKey: cstQueryKey(repoId ?? "", file ?? "", path, CST_DEPTH),
                      });
                    }}
                  />
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
