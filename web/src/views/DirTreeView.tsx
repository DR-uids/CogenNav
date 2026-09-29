import { useQueries, useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";

import type { TreeNode } from "../api/client";
import { ApiError, listRepos } from "../api/client";
import { TREE_DEPTH_DEFAULT, treeQueryOptions } from "../api/graph";
import { stateLabel } from "../components/JobProgress";
import { LanguageBar } from "../components/tree/LanguageBar";
import { DirTree } from "../components/tree/DirTree";
import { Treemap } from "../components/tree/Treemap";
import { useT } from "../i18n";
import { readDeepLinkParam, writeDeepLink } from "../lib/deepLink";
import { useReindexRepo } from "../lib/reindex";
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
  const t = useT();
  const repoId = useUi((s) => s.repoId);
  const selectedFile = useUi((s) => s.selectedFile);
  const selectFile = useUi((s) => s.selectFile);
  const selectNode = useUi((s) => s.selectNode);
  const setActiveView = useUi((s) => s.setActiveView);

  // 深链 ?dir= 只在挂载时读一次（与 CstView 一致）。
  const [selectedDir, setSelectedDir] = useState<string | null>(() => readDeepLinkParam("dir"));
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set<string>([""]));
  const [subPaths, setSubPaths] = useState<readonly string[]>([]);

  // 目录树读的是「索引产物 + 磁盘快照」：仓库还在排队/索引时快照可能尚未建出来、
  // files 表也还没写完，此时读树只会拿到 409/空数据。所以先看仓库列表里的状态，
  // 未就绪（或者列表还没回来、状态未知）就不发请求，等任务终态再读。
  const reposQuery = useQuery({
    queryKey: ["repos"],
    queryFn: ({ signal }: { signal: AbortSignal }) => listRepos(signal),
  });
  const repoSummary = reposQuery.data?.find((repo) => repo.repoId === repoId) ?? null;
  const repoState = repoSummary?.state ?? null;
  const indexing = repoState === "queued" || repoState === "running";
  const stateUnknown = Boolean(repoId) && reposQuery.isPending;

  // 410「快照已不存在」时的唯一解药：用同一个 target 再索引一次（增量；快照没了会重克隆）。
  const reindex = useReindexRepo();

  const enabled = Boolean(repoId) && !indexing && !stateUnknown;
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
  // t 进依赖：未知语言的兜底名（「其它」/ Other）也要在切语言后重算。
  const summary = useMemo(() => languageSummary(files), [files, t]);
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
  // 后端 409 = 索引进行中、快照尚未就绪（410 才是快照真被清理，需要重新索引）。
  const notReady = rootQuery.error instanceof ApiError && rootQuery.error.status === 409;
  const failed =
    enabled && !notReady && (rootQuery.isError || (!rootQuery.isLoading && !root));
  const errorMessage =
    rootQuery.error instanceof Error
      ? rootQuery.error.message
      : t("dirTree.missingNode");
  // 等索引/等列表的提示只在还没有可展示的树时出现：重新索引时保留上一次已加载的树。
  const waiting = Boolean(repoId) && !root && (indexing || notReady);

  return (
    <div data-testid="view-tree" className="flex h-full min-h-0 flex-col bg-zinc-950">
      <LanguageBar summary={summary} />

      <div className="flex min-h-0 flex-1">
        <section
          data-testid="dir-tree-panel"
          className="flex w-80 shrink-0 flex-col border-r border-zinc-800 bg-zinc-900/20"
        >
          <div className="flex h-10 shrink-0 items-center justify-between gap-2 border-b border-zinc-800 px-3">
            <h3 className="text-xs font-medium text-zinc-400">{t("dirTree.title")}</h3>
            <span className="flex shrink-0 items-center gap-2 text-[10px] text-zinc-600">
              {rootQuery.data && (
                <span data-testid="dir-tree-total">
                  {t("dirTree.total", {
                    files: t("unit.files", { count: rootQuery.data.totalFiles }),
                    loc: t("unit.lines", { count: rootQuery.data.totalLoc }),
                  })}
                </span>
              )}
              <button
                type="button"
                data-testid="dir-tree-expand-one"
                onClick={() => setExpanded(new Set([""]))}
                className="rounded border border-zinc-800 px-1.5 py-0.5 text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
              >
                {t("dirTree.oneLevel")}
              </button>
            </span>
          </div>

          {!repoId && (
            <p data-testid="dir-tree-no-repo" className="m-3 text-[11px] leading-relaxed text-zinc-500">
              {t("dirTree.pickRepo")}
            </p>
          )}

          {waiting && (
            <p
              data-testid="dir-tree-indexing"
              className="m-3 rounded border border-sky-900/60 bg-sky-950/30 p-2 text-[11px] leading-relaxed text-sky-300"
            >
              {t("dirTree.indexing", { state: stateLabel(repoState ?? "running") })}
            </p>
          )}

          {loading && (
            <p data-testid="dir-tree-loading" className="m-3 text-[11px] text-zinc-500">
              {t("dirTree.loading")}
            </p>
          )}

          {failed && (
            <div className="m-3 rounded border border-rose-900/60 bg-rose-950/30 p-2">
              <p data-testid="dir-tree-error" className="text-[11px] break-all text-rose-300">
                {t("dirTree.loadFailed", { error: errorMessage })}
              </p>
              {repoSummary && (
                <button
                  type="button"
                  data-testid="dir-tree-reindex"
                  disabled={reindex.isPending}
                  onClick={() =>
                    reindex.mutate({ target: repoSummary.target, ref: repoSummary.ref })
                  }
                  className="mt-2 rounded border border-zinc-700 bg-zinc-800 px-2 py-1 text-[11px] text-zinc-100 transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {reindex.isPending ? t("repoInput.submitting") : t("repoList.reindex")}
                </button>
              )}
              {reindex.error && (
                <p data-testid="dir-tree-reindex-error" className="mt-1.5 text-[11px] break-all text-rose-300">
                  {reindex.error instanceof Error ? reindex.error.message : t("repoList.reindexFailed")}
                </p>
              )}
            </div>
          )}

          {!loading && !failed && !waiting && rows.length > 0 && (
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
            <h3 className="shrink-0 text-xs font-medium text-zinc-400">{t("dirTree.treemapTitle")}</h3>
            <span className="truncate text-[10px] text-zinc-600">{t("dirTree.treemapHint")}</span>
            {selectedDir !== null && (
              <span
                data-testid="treemap-selected-dir"
                className="ml-auto shrink-0 truncate font-mono text-[10px] text-amber-300"
                title={selectedDir || "/"}
              >
                {t("dirTree.selectedDir", { path: selectedDir || "/" })}
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
