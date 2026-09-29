import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { searchQueryOptions } from "../../api/graph";
import type { SymbolSearchResult } from "../../api/client";
import { useT } from "../../i18n";

/** 搜索防抖：输入停顿后再请求（与文件列表同一节奏）。 */
const SEARCH_DEBOUNCE_MS = 250;
const SEARCH_LIMIT = 50;

function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

type GraphSearchProps = {
  repoId: string | null;
  /** 选中一条结果：把整条结果交给上层（既做焦点，也用于显示焦点名字）。 */
  onPick: (result: SymbolSearchResult) => void;
};

/**
 * 图谱搜索：调 `/search`，选中结果即把该节点设为焦点（力导向下会取它的邻域，
 * 分层 DAG / 影响面下会以它为根）。
 */
export function GraphSearch({ repoId, onPick }: GraphSearchProps) {
  const t = useT();
  const [raw, setRaw] = useState("");
  const keyword = raw.trim();
  const q = useDebouncedValue(keyword, SEARCH_DEBOUNCE_MS);

  const query = useQuery({
    ...searchQueryOptions(repoId ?? "", { q }, SEARCH_LIMIT),
    enabled: Boolean(repoId) && q.length > 0,
  });

  const results = query.data?.results ?? [];
  const total = query.data?.total ?? results.length;
  const pending = keyword.length > 0 && (q !== keyword || query.isLoading);

  return (
    <div className="relative min-w-0 flex-1">
      <input
        type="search"
        data-testid="graph-search-input"
        value={raw}
        placeholder={t("graphSearch.placeholder")}
        aria-label={t("graphSearch.aria")}
        onChange={(event) => setRaw(event.target.value)}
        className="w-full rounded border border-zinc-800 bg-zinc-950 px-2 py-1 font-mono text-[11px] text-zinc-200 placeholder:text-zinc-600 focus:border-zinc-600 focus:outline-none"
      />

      {keyword.length > 0 && (
        <div
          data-testid="graph-search-panel"
          className="absolute top-full left-0 z-20 mt-1 max-h-72 w-full overflow-auto rounded border border-zinc-700 bg-zinc-900/98 shadow-xl"
        >
          {pending && (
            <p data-testid="graph-search-loading" className="px-2 py-1.5 text-[10px] text-zinc-500">
              {t("graphSearch.searching")}
            </p>
          )}

          {query.isError && (
            <p data-testid="graph-search-error" className="px-2 py-1.5 text-[10px] break-all text-rose-300">
              {query.error instanceof Error ? query.error.message : t("graphSearch.failed")}
            </p>
          )}

          {!pending && !query.isError && results.length === 0 && (
            <p data-testid="graph-search-empty" className="px-2 py-1.5 text-[10px] text-zinc-500">
              {t("graphSearch.noMatch", { query: keyword })}
            </p>
          )}

          {results.length > 0 && (
            <>
              <p data-testid="graph-search-total" className="border-b border-zinc-800 px-2 py-1 text-[10px] text-zinc-500">
                {t("graphSearch.hits", { total, shown: results.length })}
              </p>
              <ul>
                {results.map((result) => (
                  <li key={result.id}>
                    <button
                      type="button"
                      data-testid="graph-search-result"
                      data-id={result.id}
                      data-kind={result.kind}
                      data-file={result.file}
                      onClick={() => {
                        onPick(result);
                        setRaw("");
                      }}
                      className="flex w-full flex-col items-start px-2 py-1 text-left hover:bg-zinc-800"
                    >
                      <span className="flex w-full items-center gap-1.5">
                        <span className="truncate font-mono text-[11px] text-zinc-100">
                          {result.name}
                        </span>
                        <span className="shrink-0 rounded bg-zinc-800 px-1 text-[9px] text-zinc-400">
                          {result.kind}
                        </span>
                      </span>
                      <span className="w-full truncate font-mono text-[10px] text-zinc-500">
                        {result.file}
                        {result.start ? `:${result.start[0] + 1}` : ""}
                      </span>
                      {result.snippet && (
                        <span className="w-full truncate text-[10px] text-zinc-600">
                          {result.snippet}
                        </span>
                      )}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
