import { useQuery } from "@tanstack/react-query";

import { symbolQueryOptions } from "../../api/graph";
import type { SymbolEdgeRef, SymbolIncoming, SymbolOutgoing } from "../../api/client";
import { formatRange } from "../../lib/cstRange";
import { useUi } from "../../stores/ui";

function positionText(edge: SymbolEdgeRef): string {
  if (!edge.start) return edge.file ?? "";
  return `${edge.file ?? ""}:${edge.start[0] + 1}`;
}

function EdgeRow({
  testId,
  nodeId,
  label,
  edge,
  onSelectNode,
  onClick,
}: {
  testId: string;
  nodeId: string;
  label: string;
  edge: SymbolEdgeRef;
  onSelectNode: (id: string) => void;
  onClick?: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        data-testid={testId}
        data-node={nodeId}
        data-relation={edge.relation}
        data-confidence={edge.confidence}
        onClick={() => {
          onSelectNode(nodeId);
          onClick?.();
        }}
        title={`${label} · ${edge.relation} · ${positionText(edge)}`}
        className="flex w-full items-baseline gap-1.5 rounded px-1 py-0.5 text-left hover:bg-zinc-800"
      >
        <span className="truncate font-mono text-[11px] text-zinc-200">{label}</span>
        <span className="shrink-0 rounded bg-zinc-800 px-1 text-[9px] text-zinc-400">
          {edge.relation}
        </span>
        {edge.confidence !== "extracted" && (
          <span
            data-testid="symbol-edge-confidence"
            className={`shrink-0 text-[9px] ${
              edge.confidence === "ambiguous" ? "text-amber-500" : "text-sky-400"
            }`}
          >
            {edge.confidence}
          </span>
        )}
      </button>
    </li>
  );
}

type SymbolCardProps = {
  repoId: string | null;
  nodeId: string;
  onSelectNode: (id: string) => void;
  /**
   * 「查看语法树」的落地动作；默认就是 selectFile + selectNode("") + setActiveView("cst")。
   * 图谱视图会传入自己的版本，额外清掉 `?node=`（图谱的符号选中与 CST 的 nodePath 同名）。
   */
  onOpenInCst?: (file: string) => void;
};

/**
 * 符号卡片（右侧 Inspector）：定义位置 + 代码片段 + 出入边列表。
 *
 * 两条跨视图联动：
 *  - 点某条边 → 换成那个符号（同视图内聚焦）
 *  - 「查看语法树」→ selectFile + selectNode("") + setActiveView("cst")
 *    （选中根节点，CST 树默认展开根，因此落地就能看到结构）
 */
export function SymbolCard({ repoId, nodeId, onSelectNode, onOpenInCst }: SymbolCardProps) {
  const selectFile = useUi((s) => s.selectFile);
  const selectNode = useUi((s) => s.selectNode);
  const setActiveView = useUi((s) => s.setActiveView);

  const query = useQuery({
    ...symbolQueryOptions(repoId ?? "", nodeId),
    enabled: Boolean(repoId && nodeId),
  });

  if (!repoId) {
    return <p className="p-3 text-[11px] text-zinc-500">先在左侧选择仓库。</p>;
  }
  if (query.isLoading) {
    return (
      <p data-testid="symbol-loading" className="p-3 text-[11px] text-zinc-500">
        正在读取符号详情…
      </p>
    );
  }
  if (query.isError) {
    return (
      <p
        data-testid="symbol-error"
        className="m-3 rounded border border-rose-900/60 bg-rose-950/30 p-2 text-[11px] break-all text-rose-300"
      >
        {query.error instanceof Error ? query.error.message : "符号详情加载失败"}
      </p>
    );
  }

  const detail = query.data;
  if (!detail) return null;

  const { node, incoming, outgoing, community, definition } = detail;

  const openInCst = () => {
    if (!node.file) return;
    if (onOpenInCst) {
      onOpenInCst(node.file);
      return;
    }
    selectFile(node.file);
    selectNode("");
    setActiveView("cst");
  };

  return (
    <div data-testid="symbol-card" className="flex flex-col gap-3 p-3 text-[11px]">
      <header>
        <div className="flex items-center gap-1.5">
          <span data-testid="symbol-name" className="truncate font-mono text-xs text-zinc-100">
            {node.name}
          </span>
          <span
            data-testid="symbol-kind"
            className="shrink-0 rounded bg-zinc-800 px-1 text-[9px] text-zinc-300"
          >
            {node.kind}
          </span>
        </div>
        <p data-testid="symbol-qualified" className="mt-1 font-mono text-[10px] break-all text-zinc-500">
          {node.qualified}
        </p>
        {community && (
          <p data-testid="symbol-community" className="mt-1 text-[10px] text-zinc-400">
            社区：{community.name}
          </p>
        )}
        <div className="mt-1 flex flex-wrap items-center gap-2 text-[10px] text-zinc-500">
          <span data-testid="symbol-degree">
            degree {node.degree}（入 {node.inDegree} / 出 {node.outDegree}）
          </span>
          {node.language && <span>{node.language}</span>}
        </div>
      </header>

      <section>
        <h4 className="mb-1 text-[10px] tracking-wide text-zinc-500 uppercase">定义</h4>
        {definition ? (
          <>
            <p data-testid="symbol-definition-file" className="font-mono break-all text-zinc-300">
              {definition.file}:{definition.start[0] + 1}
            </p>
            <p className="mb-1 font-mono text-[10px] text-zinc-600">
              {formatRange({ start: definition.start, end: definition.end })}
            </p>
            {definition.snippet && (
              <pre
                data-testid="symbol-definition"
                className="max-h-40 overflow-auto rounded border border-zinc-800 bg-zinc-950 p-2 font-mono text-[10px] leading-4 text-zinc-300"
              >
                {definition.snippet}
              </pre>
            )}
          </>
        ) : (
          <p className="text-zinc-600">后端没有给出定义位置。</p>
        )}
        <button
          type="button"
          data-testid="symbol-open-cst"
          disabled={!node.file}
          onClick={openInCst}
          className="mt-2 rounded border border-zinc-700 px-2 py-0.5 text-[10px] text-zinc-300 hover:bg-zinc-800 disabled:opacity-40"
        >
          查看语法树
        </button>
        {node.file && (
          <p className="mt-1 font-mono text-[10px] break-all text-zinc-600">{node.file}</p>
        )}
      </section>

      <section>
        <h4 className="mb-1 flex items-center gap-1 text-[10px] tracking-wide text-zinc-500 uppercase">
          入边
          <span data-testid="symbol-incoming-count" className="text-zinc-600">
            {incoming.length}
          </span>
        </h4>
        {incoming.length === 0 ? (
          <p data-testid="symbol-incoming-empty" className="text-zinc-600">
            没有符号调用/引用它。
          </p>
        ) : (
          <ul data-testid="symbol-incoming" className="space-y-0.5">
            {incoming.map((edge: SymbolIncoming) => (
              <EdgeRow
                key={`in:${edge.source}:${edge.relation}`}
                testId="symbol-incoming-item"
                nodeId={edge.source}
                label={edge.sourceName}
                edge={edge}
                onSelectNode={onSelectNode}
              />
            ))}
          </ul>
        )}
      </section>

      <section>
        <h4 className="mb-1 flex items-center gap-1 text-[10px] tracking-wide text-zinc-500 uppercase">
          出边
          <span data-testid="symbol-outgoing-count" className="text-zinc-600">
            {outgoing.length}
          </span>
        </h4>
        {outgoing.length === 0 ? (
          <p data-testid="symbol-outgoing-empty" className="text-zinc-600">
            它没有调用/引用别的符号。
          </p>
        ) : (
          <ul data-testid="symbol-outgoing" className="space-y-0.5">
            {outgoing.map((edge: SymbolOutgoing) => (
              <EdgeRow
                key={`out:${edge.target}:${edge.relation}`}
                testId="symbol-outgoing-item"
                nodeId={edge.target}
                label={edge.targetName}
                edge={edge}
                onSelectNode={onSelectNode}
              />
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
