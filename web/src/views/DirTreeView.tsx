import { useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import type { TreeNode } from "../api/client";
import { TREE_DEPTH_DEFAULT, treeQueryOptions } from "../api/graph";
import { LanguageBar } from "../components/tree/LanguageBar";
import { DirTree } from "../components/tree/DirTree";
import { Treemap } from "../components/tree/Treemap";
import { readDeepLinkParam, writeDeepLink } from "../lib/deepLink";
import { layoutTreemap } from "../lib/treemap";
import {
  ancestorDirPaths,
  buildDirRows,
  collectFiles,
  languageSummary,
  needsChildren,
  type DirRow,
} from "../lib/treeStats";
import { useUi } from "../stores/ui";

/**
 * 目录树视图（M3）：左侧目录树 + 右侧 SVG Treemap，顶部语言分布条。
 *
 * 数据流：
 *  - 根请求 `/tree?path=&depth=3` 一次拿到三层；展开更深的目录时按该目录路径再请求一次
 *    （`needsChildren`：目录没有 children 但还有文件 = 被 depth 截断）。
 *  - Treemap 与语言分布条都用「已加载的部分」计算：只画拿到的数据，不额外打请求。
 *  - 选中文件 → 写 store 并切到 CST 视图（跨视图联动的既有做法）。
 */
export function DirTreeView() {
  const repoId = useUi((s) => s.repoId);
  const selectedFile = useUi((s) => s.selectedFile);
  const selectFile = useUi((s) => s.selectFile);
  const selectNode = useUi((s) => s.selectNode);
  const setActiveView = useUi((s) => s.setActiveView);

  // 深链 ?dir= 只在挂载时读一次（与 CstView 一致）。
  const [selectedDir, setSelectedDir] = useState<string | null>(() => readDeepLinkParam("dir"));
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set<string>([""]));
  const [subPaths, setSubPaths] = useState<readonly string[]>([]);

  const enabled = Boolean(repoId);
  const rootQuery = useQuery({
    ...treeQueryOptions(repoId ?? "", "", TREE_DEPTH_DEFAULT),
    enabled,
  });
  const subQueries = useQueries({
    queries: subPaths.map((path) => ({
      ...treeQueryOptions(repoId ?? "", path, TREE_DEPTH_DEFAULT),
      enabled,
    })),
  });

  const root = rootQuery.data?.node ?? null;

  const subNodes = new Map<string, TreeNode>();
  subQueries.forEach((result, index) => {
    const path = subPaths[index];
    const node = result.data?.node;
    if (path !== undefined && node) subNodes.set(path, node);
  });
  // Map 每次渲染都是新对象，用 dataUpdatedAt 拼一个版本号当依赖。
  const subVersion = subQueries.map((result) => result.dataUpdatedAt).join("|");

  // 目录默认展开一层：根节点（path="")默认展开。
  const rows = useMemo(
    () => (root ? buildDirRows(root, expanded, subNodes) : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [root, expanded, subVersion],
  );

  // 选中目录（深链 / Treemap 点击 / 目录树点击）→ 展开它的祖先链，保证行可见。
  useEffect(() => {
    if (!selectedDir) return;
    setExpanded((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const path of ancestorDirPaths(selectedDir)) {
        if (!next.has(path)) {
          next.add(path);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [selectedDir]);

  // 选择变化 → replaceState 回写 ?dir=（首次挂载的选中态来自深链，不必回写）。
  const skipFirstWrite = useRef(true);
  useEffect(() => {
    if (skipFirstWrite.current) {
      skipFirstWrite.current = false;
      return;
    }
    writeDeepLink({ dir: selectedDir });
  }, [selectedDir]);

  // 换仓库后上一个仓库的目录选中/懒加载状态已无意义，清掉避免 404。
  const prevRepoId = useRef(repoId);
  useEffect(() => {
    if (prevRepoId.current === repoId) return;
    const hadRepo = prevRepoId.current !== null;
    prevRepoId.current = repoId;
    if (!hadRepo) return;
    setSelectedDir(null);
    setSubPaths([]);
    setExpanded(new Set([""]));
  }, [repoId]);

  const files = useMemo(() => (root ? collectFiles(root) : []), [root]);
  const summary = useMemo(() => languageSummary(files), [files]);
  const rects = useMemo(() => (root ? layoutTreemap(root) : []), [root]);

  /** 展开目录；被 depth 截断的目录顺带懒加载一层。 */
  const toggleDir = (row: DirRow) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(row.path)) next.delete(row.path);
      else next.add(row.path);
      return next;
    });
    if (needsChildren(row.node) && !subPaths.includes(row.path)) {
      setSubPaths((prev) => [...prev, row.path]);
    }
  };

  const selectDir = (path: string) => {
    setSelectedDir(path);
    if (path !== "" && !expanded.has(path)) {
      setExpanded((prev) => new Set([...prev, path]));
    }
    const node = rows.find((row) => row.path === path)?.node;
    if (node && needsChildren(node) && !subPaths.includes(path)) {
      setSubPaths((prev) => [...prev, path]);
    }
  };

  /** 打开文件：写 store + 清掉旧的 CST 节点选中 + 切到 CST 视图。 */
  const openFile = (path: string) => {
    selectFile(path);
    selectNode(null);
    setActiveView("cst");
  };

  const loading = enabled && rootQuery.isLoading;
  const failed = enabled && (rootQuery.isError || (!rootQuery.isLoading && !root));
  const errorMessage =
    rootQuery.error instanceof Error
      ? rootQuery.error.message
      : "后端未返回目录树数据（node 缺失）。";

  return (
    <div data-testid="view-tree" className="flex h-full min-h-0 flex-col bg-zinc-950">
      <LanguageBar summary={summary} />

      <div className="flex min-h-0 flex-1">
        <section
          data-testid="dir-tree-panel"
          className="flex w-80 shrink-0 flex-col border-r border-zinc-800 bg-zinc-900/20"
        >
          <div className="flex h-10 shrink-0 items-center justify-between gap-2 border-b border-zinc-800 px-3">
            <h3 className="text-xs font-medium text-zinc-400">目录</h3>
            <span className="flex shrink-0 items-center gap-2 text-[10px] text-zinc-600">
              {rootQuery.data && (
                <span data-testid="dir-tree-total">
                  共 {rootQuery.data.totalFiles} 文件 / {rootQuery.data.totalLoc} 行
                </span>
              )}
              <button
                type="button"
                data-testid="dir-tree-expand-one"
                onClick={() => setExpanded(new Set([""]))}
                className="rounded border border-zinc-800 px-1.5 py-0.5 text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
              >
                只看一层
              </button>
            </span>
          </div>

          {!repoId && (
            <p data-testid="dir-tree-no-repo" className="m-3 text-[11px] leading-relaxed text-zinc-500">
              请先在左侧「仓库」列表中选择一个仓库，这里会显示它的目录结构与 Treemap。
            </p>
          )}

          {loading && (
            <p data-testid="dir-tree-loading" className="m-3 text-[11px] text-zinc-500">
              正在加载目录树…
            </p>
          )}

          {failed && (
            <p
              data-testid="dir-tree-error"
              className="m-3 rounded border border-rose-900/60 bg-rose-950/30 p-2 text-[11px] break-all text-rose-300"
            >
              目录树加载失败：{errorMessage}
            </p>
          )}

          {enabled && !loading && !failed && rows.length > 0 && (
            <DirTree
              rows={rows}
              expanded={expanded}
              selectedDir={selectedDir}
              selectedFile={selectedFile}
              onToggle={toggleDir}
              onSelectDir={selectDir}
              onSelectFile={openFile}
            />
          )}
        </section>

        <section data-testid="treemap-panel" className="flex min-w-0 flex-1 flex-col">
          <div className="flex h-10 shrink-0 items-center gap-2 overflow-hidden border-b border-zinc-800 px-3">
            <h3 className="shrink-0 text-xs font-medium text-zinc-400">Treemap</h3>
            <span className="truncate text-[10px] text-zinc-600">
              面积 = LOC，颜色 = 语言；点矩形打开文件，点目录选中目录。
            </span>
            {selectedDir !== null && (
              <span
                data-testid="treemap-selected-dir"
                className="ml-auto shrink-0 truncate font-mono text-[10px] text-amber-300"
                title={selectedDir || "/"}
              >
                选中目录：{selectedDir || "/"}
              </span>
            )}
          </div>
          <Treemap
            rects={rects}
            selectedPath={selectedDir}
            selectedFilePath={selectedFile}
            onSelectFile={openFile}
            onSelectDir={selectDir}
          />
        </section>
      </div>
    </div>
  );
}
