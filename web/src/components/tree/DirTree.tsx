import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useRef } from "react";

import { languageColor } from "../../lib/languages";
import { needsChildren, type DirRow } from "../../lib/treeStats";

const ROW_HEIGHT = 26;

type DirTreeProps = {
  rows: readonly DirRow[];
  expanded: ReadonlySet<string>;
  selectedDir: string | null;
  selectedFile: string | null;
  onToggle: (row: DirRow) => void;
  onSelectDir: (path: string) => void;
  onSelectFile: (path: string) => void;
};

/**
 * 左侧目录树：虚拟滚动 + 可折叠。
 *
 * 虚拟化沿用 CstTree/CstFileList 的同一套做法（@tanstack/react-virtual + 绝对定位行），
 * 目录被选中（深链或 Treemap 点击）时自动滚动到该行。
 */
export function DirTree({
  rows,
  expanded,
  selectedDir,
  selectedFile,
  onToggle,
  onSelectDir,
  onSelectFile,
}: DirTreeProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 16,
  });
  const virtualItems = virtualizer.getVirtualItems();

  // 选中目录变化 → 滚到它（目录树里点目录、Treemap 点目录、深链进来都走这里）。
  useEffect(() => {
    if (!selectedDir) return;
    const index = rows.findIndex((row) => row.path === selectedDir);
    if (index >= 0) virtualizer.scrollToIndex(index, { align: "auto" });
  }, [selectedDir, rows, virtualizer]);

  return (
    <div ref={scrollRef} data-testid="dir-tree-scroll" className="min-h-0 flex-1 overflow-auto">
      <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
        {virtualItems.map((item) => {
          const row = rows[item.index];
          if (!row) return null;
          const isDir = row.type === "dir";
          const open = isDir && expanded.has(row.path);
          const selected = isDir ? row.path === selectedDir : row.path === selectedFile;
          return (
            <div
              key={row.path}
              className="absolute top-0 left-0 w-full"
              style={{ height: item.size, transform: `translateY(${item.start}px)` }}
            >
              <div
                role="treeitem"
                aria-level={row.depth + 1}
                aria-selected={selected}
                aria-expanded={isDir ? open : undefined}
                aria-label={`${isDir ? "目录" : "文件"} ${row.path || "/"}`}
                tabIndex={0}
                data-testid="dir-row"
                data-path={row.path}
                data-type={row.type}
                data-depth={row.depth}
                data-selected={selected ? "true" : "false"}
                data-expanded={isDir ? (open ? "true" : "false") : undefined}
                onClick={() => (isDir ? onSelectDir(row.path) : onSelectFile(row.path))}
                className={`flex cursor-pointer items-center gap-1.5 overflow-hidden pr-2 text-[11px] leading-6 whitespace-nowrap ${
                  selected ? "bg-zinc-800" : "hover:bg-zinc-900/70"
                }`}
                style={{ height: ROW_HEIGHT, paddingLeft: 6 + row.depth * 12 }}
              >
                {isDir ? (
                  <button
                    type="button"
                    data-testid="dir-row-toggle"
                    aria-label={`${open ? "折叠" : "展开"} ${row.path || "根目录"}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      onToggle(row);
                    }}
                    className="w-3 shrink-0 text-[9px] text-zinc-500 hover:text-zinc-200"
                  >
                    {open ? "▼" : "▶"}
                  </button>
                ) : (
                  <span
                    aria-hidden="true"
                    className="h-2 w-2 shrink-0 rounded-sm"
                    style={{ background: languageColor(row.node.language) }}
                  />
                )}

                <span
                  className={`truncate font-mono ${
                    isDir ? "text-zinc-300" : "text-zinc-400"
                  }`}
                  title={row.path || "/"}
                >
                  {row.name || "/"}
                </span>

                {isDir && needsChildren(row.node) && (
                  <span className="shrink-0 text-[9px] text-zinc-600">未展开</span>
                )}

                <span className="ml-auto flex shrink-0 items-center gap-2 tabular-nums">
                  <span data-testid="dir-row-loc" className="text-[10px] text-zinc-500">
                    {row.node.loc} 行
                  </span>
                  <span data-testid="dir-row-files" className="text-[10px] text-zinc-600">
                    {row.node.files} 文件
                  </span>
                  <span data-testid="dir-row-symbols" className="text-[10px] text-zinc-600">
                    {row.node.symbols} 符号
                  </span>
                  {row.node.errorCount > 0 && (
                    <span data-testid="dir-row-errors" className="text-[10px] text-rose-400">
                      {row.node.errorCount} 错误
                    </span>
                  )}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
