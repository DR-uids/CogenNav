import type { GraphNode } from "../../api/client";
import { useT } from "../../i18n";

type ImpactPanelProps = {
  root: GraphNode | null;
  /** 需要回归的文件（后端已按 count 排序；这里再兜一次底）。 */
  files: readonly { path: string; count: number }[];
  nodeCount: number;
  truncated: boolean;
  /** 点文件 → 去 CST 视图看它的语法树。 */
  onOpenFile: (path: string) => void;
};

/**
 * 影响面侧栏：下游闭包命中的文件清单，按命中次数排序。
 * 这是「改了这个符号，需要回归哪些文件」的直接答案，因此放在图旁边常驻，不用点节点才出现。
 */
export function ImpactPanel({
  root,
  files,
  nodeCount,
  truncated,
  onOpenFile,
}: ImpactPanelProps) {
  const t = useT();
  const ordered = [...files].sort((a, b) => b.count - a.count);

  return (
    <section data-testid="impact-panel" className="flex min-h-0 flex-col border-t border-zinc-800">
      <div className="flex shrink-0 items-center gap-2 px-3 py-2">
        <h3 className="text-[10px] tracking-wide text-zinc-500 uppercase">{t("impact.title")}</h3>
        <span data-testid="impact-file-count" className="text-[10px] text-zinc-600">
          {t("impact.fileCount", { count: ordered.length })}
        </span>
        <span data-testid="impact-node-count" className="ml-auto text-[10px] text-zinc-600">
          {t("impact.nodeCount", { count: nodeCount })}
        </span>
      </div>

      {root && (
        <p className="shrink-0 px-3 pb-1 text-[10px] text-zinc-500">
          {t("impact.root", { name: root.name })}
        </p>
      )}

      {truncated && (
        <p data-testid="impact-truncated" className="shrink-0 px-3 pb-1 text-[10px] text-amber-400">
          {t("impact.truncated")}
        </p>
      )}

      {ordered.length === 0 ? (
        <p data-testid="impact-empty" className="px-3 pb-2 text-[10px] leading-relaxed text-zinc-600">
          {t("impact.empty")}
        </p>
      ) : (
        <ul className="min-h-0 flex-1 overflow-auto px-2 pb-2">
          {ordered.map((file) => (
            <li key={file.path}>
              <button
                type="button"
                data-testid="impact-file"
                data-path={file.path}
                data-count={file.count}
                onClick={() => onOpenFile(file.path)}
                title={t("impact.fileTitle", { path: file.path, count: file.count })}
                className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-zinc-800"
              >
                <span className="truncate font-mono text-[10px] text-zinc-300">{file.path}</span>
                <span className="ml-auto shrink-0 text-[10px] text-amber-400">
                  {file.count}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
