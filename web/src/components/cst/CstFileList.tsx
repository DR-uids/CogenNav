import { useQuery } from "@tanstack/react-query";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef, useState } from "react";

import { FILE_PAGE_SIZE, listFiles, type RepoFile } from "../../api/client";
import { useUi } from "../../stores/ui";

/** 搜索防抖：输入停顿后再请求，避免每个字符都打一次后端。 */
const SEARCH_DEBOUNCE_MS = 250;
const ROW_HEIGHT = 44;

/** 简易防抖 hook（值稳定 `delay` 毫秒后才更新）。 */
function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

function FileRow({
  file,
  selected,
  onSelect,
}: {
  file: RepoFile;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      data-testid="cst-file-item"
      data-path={file.path}
      data-selected={selected ? "true" : "false"}
      title={file.error ? `${file.path}（${file.error}）` : file.path}
      onClick={onSelect}
      className={`flex w-full flex-col items-start gap-0.5 px-3 py-1.5 text-left transition-colors ${
        selected ? "bg-zinc-800" : "hover:bg-zinc-900"
      }`}
    >
      <span className="w-full truncate font-mono text-[11px] text-zinc-200">{file.path}</span>
      <span className="flex items-center gap-2 text-[10px] text-zinc-500">
        <span data-testid="cst-file-language" className="rounded bg-zinc-800 px-1 text-zinc-400">
          {file.language || "unknown"}
        </span>
        <span data-testid="cst-file-loc">{file.loc} 行</span>
        {!file.parseOk && (
          <span data-testid="cst-file-parse-error" className="text-rose-400">
            解析失败
          </span>
        )}
      </span>
    </button>
  );
}

/**
 * 左侧文件列表：搜索（防抖）+ 虚拟滚动。
 * 只负责「选文件」；CST 树与源码面板各自按 store 里的 selectedFile 取数。
 */
export function CstFileList({ repoId }: { repoId: string | null }) {
  const selectedFile = useUi((s) => s.selectedFile);
  const selectFile = useUi((s) => s.selectFile);
  const selectNode = useUi((s) => s.selectNode);

  const [rawQuery, setRawQuery] = useState("");
  const [limit, setLimit] = useState(FILE_PAGE_SIZE);
  const query = useDebouncedValue(rawQuery, SEARCH_DEBOUNCE_MS);

  // 换关键词时回到第一页，避免上一次「加载更多」的 limit 泄漏到新搜索。
  useEffect(() => {
    setLimit(FILE_PAGE_SIZE);
  }, [query, repoId]);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["files", repoId, query, limit],
    queryFn: ({ signal }) => listFiles(repoId ?? "", { q: query, limit, offset: 0 }, signal),
    enabled: Boolean(repoId),
  });

  const files = data?.files ?? [];
  const total = data?.total ?? files.length;

  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: files.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  });
  const virtualItems = virtualizer.getVirtualItems();

  const header = useMemo(() => {
    if (!repoId) return "未选择仓库";
    if (isLoading) return "正在加载…";
    if (total > files.length) return `显示 ${files.length} / 共 ${total} 个文件`;
    return `共 ${total} 个文件`;
  }, [repoId, isLoading, total, files.length]);

  return (
    <section
      data-testid="cst-file-panel"
      className="flex w-72 shrink-0 flex-col border-r border-zinc-800 bg-zinc-900/20"
    >
      <div className="flex h-10 shrink-0 items-center justify-between border-b border-zinc-800 px-3">
        <h3 className="text-xs font-medium text-zinc-400">文件</h3>
        <span data-testid="cst-file-total" className="text-[10px] text-zinc-600">
          {header}
        </span>
      </div>

      <div className="shrink-0 border-b border-zinc-800 p-2">
        <input
          type="search"
          data-testid="cst-file-search"
          value={rawQuery}
          placeholder="搜索路径…"
          aria-label="搜索文件路径"
          onChange={(event) => setRawQuery(event.target.value)}
          className="w-full rounded border border-zinc-800 bg-zinc-950 px-2 py-1 font-mono text-[11px] text-zinc-200 placeholder:text-zinc-600 focus:border-zinc-600 focus:outline-none"
        />
      </div>

      {!repoId && (
        <p data-testid="cst-no-repo" className="m-3 text-[11px] leading-relaxed text-zinc-500">
          请先在左侧「仓库」列表中选择一个仓库，这里会列出它的文件。
        </p>
      )}

      {repoId && isError && (
        <p
          data-testid="cst-files-error"
          className="m-3 rounded border border-rose-900/60 bg-rose-950/30 p-2 text-[11px] break-all text-rose-300"
        >
          {error instanceof Error ? error.message : "文件列表加载失败"}
        </p>
      )}

      {repoId && !isError && !isLoading && files.length === 0 && (
        <p data-testid="cst-files-empty" className="m-3 text-[11px] leading-relaxed text-zinc-500">
          {query ? `没有匹配「${query}」的文件。` : "该仓库没有可浏览的文件。"}
        </p>
      )}

      {repoId && isLoading && files.length === 0 && (
        <p data-testid="cst-files-loading" className="m-3 text-[11px] text-zinc-500">
          正在加载文件列表…
        </p>
      )}

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto" data-testid="cst-file-scroll">
        <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
          {virtualItems.map((item) => {
            const file = files[item.index];
            if (!file) return null;
            return (
              <div
                key={file.path}
                className="absolute top-0 left-0 w-full"
                style={{ height: item.size, transform: `translateY(${item.start}px)` }}
              >
                <FileRow
                  file={file}
                  selected={file.path === selectedFile}
                  onSelect={() => {
                    selectFile(file.path);
                    // 换文件时旧节点的 nodePath 已经无意义，清掉选中节点。
                    selectNode(null);
                  }}
                />
              </div>
            );
          })}
        </div>
      </div>

      {total > files.length && (
        <button
          type="button"
          data-testid="cst-files-more"
          onClick={() => setLimit((prev) => prev + FILE_PAGE_SIZE)}
          className="shrink-0 border-t border-zinc-800 px-3 py-1.5 text-[11px] text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
        >
          加载更多（剩余 {total - files.length} 个）
        </button>
      )}
    </section>
  );
}
