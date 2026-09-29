import { useState } from "react";

import { useT } from "../../i18n";
import { languageColor } from "../../lib/languages";
import type { LanguageStat } from "../../lib/treeStats";

/** 统计口径：按行数（默认）或按文件数。 */
type Metric = "loc" | "files";

function percent(value: number, total: number): string {
  if (total <= 0) return "0%";
  const ratio = (value / total) * 100;
  if (ratio > 0 && ratio < 0.1) return "<0.1%";
  return `${Math.round(ratio * 10) / 10}%`;
}

/**
 * 顶部语言分布条：同一个色板（lib/languages）与 Treemap 共用，因此条形与矩形颜色一一对应。
 * 两种口径（文件数 / 行数）由用户切换，避免「大文件语言被小文件数量淹没」。
 */
export function LanguageBar({ summary }: { summary: readonly LanguageStat[] }) {
  const t = useT();
  const [metric, setMetric] = useState<Metric>("loc");
  const value = (stat: LanguageStat) => (metric === "loc" ? stat.loc : stat.files);
  const items = [...summary].sort((a, b) => value(b) - value(a));
  const total = items.reduce((sum, stat) => sum + value(stat), 0);

  return (
    <section
      data-testid="lang-bar-panel"
      className="shrink-0 border-b border-zinc-800 bg-zinc-900/20 px-3 py-2"
    >
      <div className="flex items-center gap-3">
        <h3 className="text-xs font-medium text-zinc-400">{t("langBar.title")}</h3>
        <span data-testid="lang-bar-total" className="text-[10px] text-zinc-600">
          {metric === "loc"
            ? t("unit.lines", { count: total })
            : t("langBar.totalFiles", { count: total })}
        </span>
        <div className="ml-auto flex items-center gap-1" role="group" aria-label={t("langBar.metricAria")}>
          <button
            type="button"
            data-testid="lang-mode-loc"
            aria-pressed={metric === "loc"}
            onClick={() => setMetric("loc")}
            className={`rounded px-2 py-0.5 text-[10px] ${
              metric === "loc"
                ? "bg-zinc-800 text-zinc-100"
                : "text-zinc-500 hover:bg-zinc-900 hover:text-zinc-300"
            }`}
          >
            {t("langBar.byLoc")}
          </button>
          <button
            type="button"
            data-testid="lang-mode-files"
            aria-pressed={metric === "files"}
            onClick={() => setMetric("files")}
            className={`rounded px-2 py-0.5 text-[10px] ${
              metric === "files"
                ? "bg-zinc-800 text-zinc-100"
                : "text-zinc-500 hover:bg-zinc-900 hover:text-zinc-300"
            }`}
          >
            {t("langBar.byFiles")}
          </button>
        </div>
      </div>

      <div
        data-testid="lang-bar"
        className="mt-1.5 flex h-2 w-full overflow-hidden rounded bg-zinc-800"
        aria-label={t("langBar.barAria")}
      >
        {items.map((stat) => {
          const share = total > 0 ? (value(stat) / total) * 100 : 0;
          return (
            <div
              key={stat.language}
              data-testid="lang-bar-segment"
              data-language={stat.language}
              data-value={value(stat)}
              title={`${stat.language} ${percent(value(stat), total)}`}
              style={{ width: `${share}%`, background: languageColor(stat.language) }}
            />
          );
        })}
      </div>

      {items.length > 0 ? (
        <ul className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5">
          {items.map((stat) => (
            <li
              key={stat.language}
              data-testid="lang-legend-item"
              data-language={stat.language}
              data-value={value(stat)}
              className="flex items-center gap-1 text-[10px] text-zinc-400"
            >
              <span
                aria-hidden="true"
                className="h-2 w-2 rounded-sm"
                style={{ background: languageColor(stat.language) }}
              />
              {stat.language}
              <span className="text-zinc-600">{percent(value(stat), total)}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1.5 text-[10px] text-zinc-600">{t("langBar.empty")}</p>
      )}
    </section>
  );
}
