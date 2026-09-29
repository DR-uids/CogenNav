import { useMemo, useState, type MouseEvent } from "react";

import { useT } from "../../i18n";
import { languageColor, languageLabel } from "../../lib/languages";
import { TREEMAP_HEIGHT, TREEMAP_WIDTH, type TreemapRect } from "../../lib/treemap";

type TreemapProps = {
  rects: readonly TreemapRect[];
  /** 选中的目录（画白描边）。 */
  selectedPath: string | null;
  /** 选中的文件（在 CST 视图里正打开的那个，画白描边）。 */
  selectedFilePath?: string | null;
  onSelectFile: (path: string) => void;
  onSelectDir: (path: string) => void;
};

/** 一个文件矩形 + 高亮态。 */
function FileCell({
  rect,
  selected,
  onSelect,
  onHover,
  onLeave,
}: {
  rect: TreemapRect;
  selected: boolean;
  onSelect: () => void;
  onHover: (rect: TreemapRect, event: MouseEvent<SVGRectElement>) => void;
  onLeave: () => void;
}) {
  const t = useT();
  const tiny = rect.width < 3 || rect.height < 3;
  return (
    <rect
      data-testid="treemap-rect"
      data-path={rect.path}
      data-language={rect.language ?? ""}
      data-loc={rect.loc}
      data-files={rect.files}
      data-symbols={rect.symbols}
      role="button"
      tabIndex={-1}
      aria-label={t("treemap.fileAria", { path: rect.path })}
      x={rect.x}
      y={rect.y}
      width={rect.width}
      height={rect.height}
      fill={languageColor(rect.language)}
      fillOpacity={selected ? 1 : tiny ? 0.75 : 0.9}
      stroke={selected ? "#fafafa" : "#18181b"}
      strokeWidth={selected ? 2 : 0.5}
      className="cursor-pointer"
      onClick={onSelect}
      onMouseEnter={(event) => onHover(rect, event)}
      onMouseMove={(event) => onHover(rect, event)}
      onMouseLeave={onLeave}
    />
  );
}

/**
 * 右侧 SVG Treemap：矩形由 `lib/treemap.ts`（d3-hierarchy 纯计算）给出，这里只负责画。
 *
 * 目录矩形画在文件矩形之前（DOM 顺序即层叠顺序），点空白/间隙会命中目录矩形 → 选中该目录；
 * 文件矩形在最上层，点它就是「打开文件」。hover 时跟随鼠标给出路径/loc/文件数/符号数。
 */
export function Treemap({
  rects,
  selectedPath,
  selectedFilePath = null,
  onSelectFile,
  onSelectDir,
}: TreemapProps) {
  const t = useT();
  const [hover, setHover] = useState<{ rect: TreemapRect; x: number; y: number } | null>(null);

  // 目录先画（面积大、铺在底层），文件后画；同类型按面积从大到小，视觉更稳。
  const ordered = useMemo(
    () =>
      [...rects].sort((a, b) => {
        if (a.type !== b.type) return a.type === "dir" ? -1 : 1;
        if (a.type === "dir") return a.depth - b.depth;
        return b.width * b.height - a.width * a.height;
      }),
    [rects],
  );

  const onHover = (rect: TreemapRect, event: MouseEvent<SVGRectElement>) => {
    const bounds = event.currentTarget.ownerSVGElement?.getBoundingClientRect();
    const left = event.clientX && bounds ? event.clientX - bounds.left + 12 : rect.x + 4;
    const top = event.clientY && bounds ? event.clientY - bounds.top + 12 : rect.y + 4;
    setHover({ rect, x: left, y: top });
  };

  return (
    <div className="relative min-h-0 flex-1">
      <svg
        data-testid="treemap-svg"
        viewBox={`0 0 ${TREEMAP_WIDTH} ${TREEMAP_HEIGHT}`}
        preserveAspectRatio="xMidYMid meet"
        className="h-full w-full"
        role="img"
        aria-label={t("treemap.svgAria")}
      >
        {ordered.map((rect) =>
          rect.type === "dir" ? (
            <rect
              key={`dir:${rect.path}`}
              data-testid="treemap-dir"
              data-path={rect.path}
              data-loc={rect.loc}
              data-files={rect.files}
              data-symbols={rect.symbols}
              x={rect.x}
              y={rect.y}
              width={rect.width}
              height={rect.height}
              fill="transparent"
              stroke={selectedPath === rect.path ? "#e4e4e7" : "#3f3f46"}
              strokeWidth={selectedPath === rect.path ? 1.5 : 0.6}
              className="cursor-pointer"
              onClick={() => onSelectDir(rect.path)}
              onMouseEnter={(event) => onHover(rect, event)}
              onMouseMove={(event) => onHover(rect, event)}
              onMouseLeave={() => setHover(null)}
            />
          ) : (
            <FileCell
              key={`file:${rect.path}`}
              rect={rect}
              selected={selectedFilePath === rect.path}
              onSelect={() => onSelectFile(rect.path)}
              onHover={onHover}
              onLeave={() => setHover(null)}
            />
          ),
        )}
      </svg>

      {rects.length === 0 && (
        <p
          data-testid="treemap-empty"
          className="absolute inset-0 flex items-center justify-center text-xs text-zinc-500"
        >
          {t("treemap.empty")}
        </p>
      )}

      {hover && (
        <div
          data-testid="treemap-tooltip"
          className="pointer-events-none absolute z-10 max-w-xs rounded border border-zinc-700 bg-zinc-900/95 p-2 text-[10px] shadow-lg"
          style={{ left: hover.x, top: hover.y }}
        >
          <p data-testid="treemap-tooltip-path" className="mb-1 font-mono break-all text-zinc-200">
            {hover.rect.path || "/"}
          </p>
          <p className="text-zinc-400">
            {t("treemap.language")}
            <span data-testid="treemap-tooltip-language">{languageLabel(hover.rect.language)}</span>
          </p>
          <p className="text-zinc-400">
            {t("treemap.loc")}
            <span data-testid="treemap-tooltip-loc">{hover.rect.loc}</span>
          </p>
          <p className="text-zinc-400">
            {t("treemap.files")}
            <span data-testid="treemap-tooltip-files">{hover.rect.files}</span>
          </p>
          <p className="text-zinc-400">
            {t("treemap.symbols")}
            <span data-testid="treemap-tooltip-symbols">{hover.rect.symbols}</span>
          </p>
        </div>
      )}
    </div>
  );
}
