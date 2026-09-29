import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { CST_DEPTH, cstQueryOptions } from "../../api/cst";
import { getFileText } from "../../api/client";
import { useT } from "../../i18n";
import {
  SOURCE_MARK_CLASS,
  byteColumnToCharColumn,
  formatRange,
  rangeKey,
  splitSourceLines,
  stripLineEnding,
  type CstRange,
} from "../../lib/cstRange";
import { highlightToHtml } from "../../lib/shiki";
import { useUi } from "../../stores/ui";

/** 纯文本回退一次最多渲染这么多行，避免超大文件卡住主线程。 */
const MAX_PLAIN_LINES = 1500;
/** 超过这个行数直接不做高亮（shiki 是逐行 tokenize，行数太大不划算）。 */
const MAX_HIGHLIGHT_LINES = 2000;

type LineRange = { startLine: number; endLine: number; startChar: number; endChar: number };

function lineRangeOf(lines: readonly string[], range: CstRange | null): LineRange | null {
  if (!range || lines.length === 0) return null;
  const startLine = Math.min(Math.max(range.start[0], 0), lines.length - 1);
  const endLine = Math.min(Math.max(range.end[0], startLine), lines.length - 1);
  return {
    startLine,
    endLine,
    startChar: byteColumnToCharColumn(lines[startLine] ?? "", range.start[1]),
    endChar: byteColumnToCharColumn(lines[endLine] ?? "", range.end[1]),
  };
}

/**
 * 纯文本回退：按行加背景，并在选中范围的首/尾行做列级 `<mark>`。
 * shiki 不可用（wasm 加载失败、语言不支持、超时）时走这里，保证功能不缺失。
 */
function PlainSource({ text, range }: { text: string; range: CstRange | null }) {
  const t = useT();
  const lines = useMemo(() => splitSourceLines(text), [text]);
  const lineRange = useMemo(() => lineRangeOf(lines, range), [lines, range]);
  const visible = lines.slice(0, MAX_PLAIN_LINES);

  return (
    <pre data-testid="cst-source-plain" className="m-0 p-3 font-mono text-[11px] leading-5">
      {visible.map((line, index) => {
        const content = stripLineEnding(line);
        const active =
          lineRange !== null && index >= lineRange.startLine && index <= lineRange.endLine;
        let body: ReactNode = content;
        if (active && lineRange) {
          const from = index === lineRange.startLine ? lineRange.startChar : 0;
          const to = index === lineRange.endLine ? lineRange.endChar : content.length;
          const head = content.slice(0, from);
          const middle = content.slice(from, Math.max(to, from));
          const tail = content.slice(Math.max(to, from));
          body = (
            <>
              {head}
              {middle.length > 0 && (
                <mark
                  className={SOURCE_MARK_CLASS}
                  data-testid="cst-source-mark"
                  title={t("cstSource.selectionTitle")}
                >
                  {middle}
                </mark>
              )}
              {tail}
            </>
          );
        }
        return (
          <div
            key={index}
            data-line={index + 1}
            data-testid={active && lineRange?.startLine === index ? "cst-source-active-line" : undefined}
            className={active ? "bg-amber-500/10" : undefined}
          >
            <span className="mr-3 inline-block w-8 shrink-0 text-right text-zinc-600 select-none">
              {index + 1}
            </span>
            {body}
          </div>
        );
      })}
      {lines.length > visible.length && (
        <div className="mt-2 text-[10px] text-zinc-500">
          {t("cstSource.truncatedLines", { limit: MAX_PLAIN_LINES, total: lines.length })}
        </div>
      )}
    </pre>
  );
}

/**
 * 右侧源码面板：拉取文件文本并用 shiki 高亮，选中节点时高亮其字节/行列范围并滚动到可见处。
 *
 * 节点范围不走 store：源码面板直接用与树相同的 queryKey 请求 `nodePath`，
 * 命中缓存时零开销，深链进来（树还没展开）也能定位。
 */
export function CstSource({ repoId, file }: { repoId: string | null; file: string | null }) {
  const t = useT();
  const selectedNode = useUi((s) => s.selectedNode);
  const scrollRef = useRef<HTMLDivElement>(null);

  const enabled = Boolean(repoId && file);
  const fileQuery = useQuery({
    queryKey: ["file", repoId, file],
    queryFn: ({ signal }) => getFileText(repoId ?? "", file ?? "", signal),
    enabled,
  });

  const nodeQuery = useQuery({
    // 未选中时用哨兵路径，避免和树的根请求（nodePath=""）共用同一个 key。
    ...cstQueryOptions(repoId ?? "", file ?? "", selectedNode ?? "__none__", CST_DEPTH),
    enabled: enabled && selectedNode !== null,
  });

  const text = fileQuery.data?.text ?? "";
  const language = fileQuery.data?.language ?? null;
  const lines = useMemo(() => splitSourceLines(text), [text]);
  const node = selectedNode !== null ? (nodeQuery.data?.node ?? null) : null;
  // 后端截断源码时节点行号可能越界，此时不高亮（比标到最后一行更诚实）。
  const range: CstRange | null =
    node && node.start[0] < lines.length ? { start: node.start, end: node.end } : null;
  const key = rangeKey(range);

  const [html, setHtml] = useState<string | null>(null);

  // 高亮是渐进增强：先渲染纯文本，成功后再替换成 shiki 的 HTML。
  useEffect(() => {
    let alive = true;
    if (!text || lines.length > MAX_HIGHLIGHT_LINES) {
      setHtml(null);
      return () => {
        alive = false;
      };
    }
    highlightToHtml(text, language, range ? [range] : [])
      .then((result) => {
        if (alive) setHtml(result);
      })
      .catch(() => {
        if (alive) setHtml(null);
      });
    return () => {
      alive = false;
    };
    // range 的内容由 key 唯一确定，用 key 作依赖避免每次渲染都重新高亮。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [text, lines, language, key]);

  // 选中节点后把范围标记滚动到视野中间（jsdom 未实现 scrollIntoView，忽略即可）。
  useEffect(() => {
    const container = scrollRef.current;
    if (!container) return;
    const target = container.querySelector(`mark.${SOURCE_MARK_CLASS}`);
    if (!(target instanceof HTMLElement)) return;
    try {
      target.scrollIntoView({ block: "center" });
    } catch {
      // 测试环境（jsdom）没有布局，忽略。
    }
  }, [html, key, text]);

  const error = fileQuery.error;

  return (
    <section data-testid="cst-source-panel" className="flex w-[40%] min-w-0 shrink-0 flex-col">
      <div className="flex h-10 shrink-0 items-center gap-2 overflow-hidden border-b border-zinc-800 px-3">
        <h3 className="shrink-0 text-xs font-medium text-zinc-400">{t("cstSource.title")}</h3>
        {language && (
          <span data-testid="cst-source-language" className="shrink-0 rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400">
            {language}
          </span>
        )}
        {node && range && (
          <span
            data-testid="cst-source-node"
            className="truncate font-mono text-[10px] text-amber-300"
            title={`${node.type} ${formatRange(range)}`}
          >
            {node.type} · {formatRange(range)}
          </span>
        )}
        {selectedNode !== null && !node && nodeQuery.isLoading && (
          <span data-testid="cst-source-node-loading" className="text-[10px] text-zinc-500">
            {t("cstSource.locating")}
          </span>
        )}
      </div>

      {!file && (
        <p data-testid="cst-source-empty" className="m-4 text-xs leading-relaxed text-zinc-500">
          {t("cstSource.pickFile")}
        </p>
      )}

      {file && fileQuery.isLoading && (
        <p data-testid="cst-source-loading" className="m-4 text-xs text-zinc-500">
          {t("cstSource.loading")}
        </p>
      )}

      {file && error && (
        <p
          data-testid="cst-source-error"
          className="m-4 rounded border border-rose-900/60 bg-rose-950/30 p-3 text-xs break-all text-rose-300"
        >
          {error instanceof Error ? error.message : t("cstSource.loadFailed")}
        </p>
      )}

      {file && !error && text && (
        <div ref={scrollRef} data-testid="cst-source-scroll" className="min-h-0 flex-1 overflow-auto">
          {fileQuery.data?.truncated && (
            <p className="border-b border-amber-900/40 bg-amber-950/20 px-3 py-1 text-[10px] text-amber-300">
              {t("cstSource.truncated")}
            </p>
          )}
          {html ? (
            <div
              data-testid="cst-source-highlighted"
              // shiki 的输出是自生成的 HTML（无外部输入拼接），可安全注入。
              dangerouslySetInnerHTML={{ __html: html }}
            />
          ) : (
            <PlainSource text={text} range={range} />
          )}
        </div>
      )}

      {file && !error && !fileQuery.isLoading && !text && (
        <p className="m-4 text-xs text-zinc-500">{t("cstSource.emptyFile")}</p>
      )}
    </section>
  );
}
