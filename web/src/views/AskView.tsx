/**
 * AI 问答视图（M4）：左侧「AI 状态 + 快捷问题 + 社区命名/架构摘要」，
 * 中间对话区，右侧「引用 + 工具轨迹」。
 *
 * 与其它视图一致的约定：
 *  - 选中态进 `useUi`，引用点击 = `selectNode(id, "graph")` + 切到图谱视图；
 *  - 深链参数直接读写 `window.location.search`（`?view=ask` 由 App 处理，`?q=` 记住提问）；
 *  - 渲染层不直接碰 fetch/reader：SSE 的分帧解析在 `api/ask.ts`，
 *    状态机在 `lib/askStream.ts`，这里只做接线与渲染。
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import {
  askErrorMessage,
  getAiStatus,
  getSummary,
  nameCommunities,
  streamAsk,
  type AskCitation,
  type AskStream,
} from "../api/ask";
import { useT, type MessageKey } from "../i18n";
import { readDeepLinkParam, writeDeepLink } from "../lib/deepLink";
import {
  applyAskEvent,
  collectCitations,
  collectToolTraces,
  conversationHistory,
  emptyConversation,
  isStreaming,
  sendQuestion,
  stopStreaming,
  type AskConversation,
  type AskToolTrace,
  type ChatMessage,
} from "../lib/askStream";
import { useUi } from "../stores/ui";

/** 快捷问题：覆盖「入口 / 调用方 / 影响面 / 结构」四类常见问法（存文案键，切语言时跟着变）。 */
const QUICK_QUESTION_KEYS: readonly MessageKey[] = [
  "ask.quick.entry",
  "ask.quick.whoCalls",
  "ask.quick.impact",
  "ask.quick.godNodes",
];

const CHIP_CLASS =
  "flex items-center gap-1 rounded border border-zinc-700 bg-zinc-900 px-1.5 py-0.5 text-left hover:border-zinc-500 hover:bg-zinc-800";

/** 引用 chip：显示 name + kind，点击跳到图谱里的该符号。 */
function CitationChip({
  citation,
  onOpen,
}: {
  citation: AskCitation;
  onOpen: (id: string) => void;
}) {
  return (
    <button
      type="button"
      data-testid="ask-citation"
      data-id={citation.id}
      data-kind={citation.kind}
      title={citation.file ? `${citation.file} · ${citation.kind}` : citation.name}
      onClick={() => onOpen(citation.id)}
      className={CHIP_CLASS}
    >
      <span className="max-w-48 truncate font-mono text-[10px] text-zinc-200">{citation.name}</span>
      <span className="shrink-0 rounded bg-zinc-800 px-1 text-[9px] text-zinc-400">
        {citation.kind}
      </span>
    </button>
  );
}

/** 单条回答的工具轨迹：默认折叠，点开后看名称 / 参数 JSON / 结果摘要。 */
function ToolTrace({ tools }: { tools: AskToolTrace[] }) {
  const t = useT();
  const [open, setOpen] = useState(false);

  return (
    <div data-testid="ask-message-tools" className="mt-2">
      <button
        type="button"
        data-testid="ask-tools-toggle"
        aria-expanded={open}
        onClick={() => setOpen((prev) => !prev)}
        className="rounded border border-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
      >
        {t("unit.toolCalls", { count: tools.length })} {open ? "▾" : "▸"}
      </button>
      {open && (
        <ul data-testid="ask-tool-list" className="mt-1 space-y-1">
          {tools.map((tool, index) => (
            <li
              key={`${tool.name}-${index}`}
              data-testid="ask-tool-item"
              data-tool={tool.name}
              className="rounded border border-zinc-800 bg-zinc-950/60 p-2"
            >
              <p data-testid="ask-tool-name" className="font-mono text-[10px] text-zinc-300">
                {tool.name}
              </p>
              <pre
                data-testid="ask-tool-args"
                className="mt-1 overflow-auto font-mono text-[10px] text-zinc-500"
              >
                {JSON.stringify(tool.arguments)}
              </pre>
              <p data-testid="ask-tool-summary" className="mt-1 text-[10px] text-zinc-400">
                {tool.summary ?? t("ask.toolPending")}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** 一条消息：用户气泡靠右，助手气泡靠左（含工具轨迹与引用）。 */
function MessageRow({
  message,
  onOpenCitation,
}: {
  message: ChatMessage;
  onOpenCitation: (id: string) => void;
}) {
  const t = useT();
  const isUser = message.role === "user";

  return (
    <article
      data-testid="ask-message"
      data-role={message.role}
      data-status={message.status}
      className={`flex ${isUser ? "justify-end" : "justify-start"}`}
    >
      <div
        className={`max-w-[85%] rounded-lg border px-3 py-2 ${
          isUser ? "border-sky-900/60 bg-sky-950/30" : "border-zinc-800 bg-zinc-900/40"
        }`}
      >
        <p className="mb-1 text-[10px] tracking-wide text-zinc-500">
          {isUser ? t("ask.role.user") : t("ask.role.assistant")}
        </p>

        {message.content ? (
          <p
            data-testid="ask-message-content"
            className="text-[12px] leading-relaxed whitespace-pre-wrap text-zinc-100"
          >
            {message.content}
            {message.status === "streaming" && <span className="ml-0.5 text-sky-400">▍</span>}
          </p>
        ) : message.status === "streaming" ? (
          <p data-testid="ask-message-content" className="text-[12px] text-zinc-500">
            {t("ask.retrieving")}
          </p>
        ) : null}

        {message.status === "stopped" && (
          <p data-testid="ask-message-stopped" className="mt-1 text-[10px] text-zinc-500">
            {t("ask.stopped")}
          </p>
        )}

        {message.error && (
          <p
            data-testid="ask-message-error"
            className="mt-1 rounded border border-rose-900/60 bg-rose-950/30 px-2 py-1 text-[11px] break-all text-rose-300"
          >
            {message.error}
          </p>
        )}

        {!isUser && message.tools.length > 0 && <ToolTrace tools={message.tools} />}

        {!isUser && message.citations.length > 0 && (
          <div data-testid="ask-message-citations" className="mt-2 flex flex-wrap gap-1">
            {message.citations.map((citation) => (
              <CitationChip key={citation.id} citation={citation} onOpen={onOpenCitation} />
            ))}
          </div>
        )}
      </div>
    </article>
  );
}

export function AskView() {
  const t = useT();
  const repoId = useUi((s) => s.repoId);
  const selectNode = useUi((s) => s.selectNode);
  const setActiveView = useUi((s) => s.setActiveView);
  const queryClient = useQueryClient();

  const [conversation, setConversation] = useState<AskConversation>(emptyConversation);
  // 深链 ?q=：刷新后把上次的提问填回输入框（不自动发送，避免刷新就花钱）。
  const [draft, setDraft] = useState(() => readDeepLinkParam("q") ?? "");
  const streamRef = useRef<AskStream | null>(null);
  const busy = isStreaming(conversation);

  const aiStatusQuery = useQuery({
    queryKey: ["aiStatus", repoId ?? ""],
    queryFn: () => getAiStatus(repoId ?? ""),
    enabled: Boolean(repoId),
    staleTime: 60_000,
    retry: false,
  });
  const status = aiStatusQuery.data;
  // 状态未知时先当作可用：后端未配置时会用 error 事件给出明确文案。
  const configured = status ? status.configured : true;

  // 卸载时中止未完成的流，避免对已卸载组件 setState。
  useEffect(() => {
    const ref = streamRef;
    return () => {
      ref.current?.cancel();
      ref.current = null;
    };
  }, []);

  // 换仓库：上一个仓库的对话没有意义，清掉并断流。
  const prevRepoId = useRef(repoId);
  useEffect(() => {
    if (prevRepoId.current === repoId) return;
    prevRepoId.current = repoId;
    streamRef.current?.cancel();
    streamRef.current = null;
    setConversation(emptyConversation);
  }, [repoId]);

  const cancelStream = () => {
    streamRef.current?.cancel();
    streamRef.current = null;
  };

  const send = (raw: string) => {
    const question = raw.trim();
    if (!question || busy || !repoId || !configured) return;

    const history = conversationHistory(conversation);
    setDraft("");
    writeDeepLink({ q: question });
    setConversation((prev) => sendQuestion(prev, question));

    streamRef.current = streamAsk(repoId, { question, history }, {
      onTool: (call) =>
        setConversation((prev) => applyAskEvent(prev, { type: "tool", ...call })),
      onToolResult: (result) =>
        setConversation((prev) => applyAskEvent(prev, { type: "tool_result", ...result })),
      onDelta: (text) => setConversation((prev) => applyAskEvent(prev, { type: "delta", text })),
      onDone: (payload) => {
        streamRef.current = null;
        setConversation((prev) => applyAskEvent(prev, { type: "done", ...payload }));
      },
      onError: (message) => {
        streamRef.current = null;
        setConversation((prev) => applyAskEvent(prev, { type: "error", message }));
      },
    });
  };

  const stop = () => {
    cancelStream();
    setConversation((prev) => stopStreaming(prev));
  };

  /** 引用点击：写入 store（origin=graph，与 CST 的 nodePath 区分）并切到图谱视图。 */
  const openCitation = (id: string) => {
    selectNode(id, "graph");
    setActiveView("graph");
  };

  const naming = useMutation({
    mutationFn: (force: boolean) => nameCommunities(repoId ?? "", force),
    onSuccess: () => {
      // 社区名出现在 /analysis（图例）与 /graph、/symbol（卡片）里：全部失效重取。
      for (const key of ["analysis", "graph", "neighbors", "impact", "symbol"]) {
        void queryClient.invalidateQueries({ queryKey: [key, repoId ?? ""] });
      }
    },
  });

  const summary = useMutation({ mutationFn: () => getSummary(repoId ?? "") });

  const citations = collectCitations(conversation);
  const traces = collectToolTraces(conversation);
  const canAsk = Boolean(repoId) && configured && !busy;
  const inputDisabled = !canAsk;
  const summaryLines = (summary.data?.summary ?? "")
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);

  return (
    <div data-testid="view-ask" className="flex h-full min-h-0 bg-zinc-950">
      {/* 左栏：AI 状态 / 快捷问题 / 社区命名与架构摘要 */}
      <aside className="flex w-72 shrink-0 flex-col overflow-auto border-r border-zinc-800 bg-zinc-900/20">
        <section data-testid="ask-status" className="border-b border-zinc-800 px-3 py-2">
          <h3 className="text-[10px] tracking-wide text-zinc-500 uppercase">{t("ask.status.title")}</h3>
          {!repoId ? (
            <p data-testid="ask-no-repo" className="mt-1 text-[11px] leading-relaxed text-zinc-500">
              {t("ask.status.noRepo")}
            </p>
          ) : aiStatusQuery.isLoading ? (
            <p data-testid="ask-status-loading" className="mt-1 text-[11px] text-zinc-500">
              {t("ask.status.loading")}
            </p>
          ) : aiStatusQuery.isError ? (
            <p data-testid="ask-status-error" className="mt-1 text-[11px] text-rose-300">
              {askErrorMessage(aiStatusQuery.error)}
            </p>
          ) : status ? (
            <dl className="mt-1 space-y-1 text-[10px] text-zinc-400">
              <div className="flex items-center gap-1">
                <dt className="text-zinc-500">{t("ask.status.model")}</dt>
                <dd data-testid="ask-status-model" className="truncate font-mono text-zinc-200">
                  {status.model || t("ask.status.modelUnset")}
                </dd>
              </div>
              <div className="flex items-center gap-1">
                <dt className="text-zinc-500">{t("ask.status.rag")}</dt>
                <dd data-testid="ask-status-rag" className="font-mono text-zinc-300">
                  {status.rag}
                </dd>
                <dt className="text-zinc-500">{t("ask.status.redact")}</dt>
                <dd>{status.redact ? t("ask.status.on") : t("ask.status.off")}</dd>
              </div>
              <div>
                <dt className="text-zinc-500">{t("ask.status.tools", { count: status.tools.length })}</dt>
                <dd data-testid="ask-status-tools" className="mt-0.5 font-mono leading-4 break-all text-zinc-500">
                  {status.tools.join(" / ") || t("ask.status.toolsNone")}
                </dd>
              </div>
            </dl>
          ) : null}
        </section>

        {status && !status.configured && (
          <p
            data-testid="ask-llm-warning"
            className="border-b border-amber-900/50 bg-amber-950/20 px-3 py-2 text-[11px] leading-relaxed text-amber-300"
          >
            {t("ask.llmWarning")}
          </p>
        )}

        <section data-testid="ask-quick" className="border-b border-zinc-800 px-3 py-2">
          <h3 className="text-[10px] tracking-wide text-zinc-500 uppercase">{t("ask.quick.title")}</h3>
          <div className="mt-1 flex flex-col gap-1">
            {QUICK_QUESTION_KEYS.map((questionKey) => (
              <button
                key={questionKey}
                type="button"
                data-testid="ask-quick-item"
                disabled={!canAsk}
                onClick={() => send(t(questionKey))}
                className="rounded border border-zinc-800 px-2 py-1 text-left text-[11px] text-zinc-300 hover:bg-zinc-900 hover:text-zinc-100 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {t(questionKey)}
              </button>
            ))}
          </div>
        </section>

        <section data-testid="ask-community-panel" className="px-3 py-2">
          <h3 className="text-[10px] tracking-wide text-zinc-500 uppercase">{t("ask.community.title")}</h3>
          <p className="mt-1 text-[10px] leading-relaxed text-zinc-600">
            {t("ask.community.hint")}
          </p>

          <div className="mt-2 flex flex-wrap gap-1">
            <button
              type="button"
              data-testid="ask-name-communities"
              disabled={!repoId || naming.isPending}
              onClick={() => naming.mutate(false)}
              className="rounded border border-zinc-700 px-2 py-0.5 text-[11px] text-zinc-200 hover:bg-zinc-800 disabled:opacity-40"
            >
              {naming.isPending ? t("ask.community.naming") : t("ask.community.name")}
            </button>
            <button
              type="button"
              data-testid="ask-name-communities-force"
              disabled={!repoId || naming.isPending}
              onClick={() => naming.mutate(true)}
              className="rounded border border-zinc-800 px-2 py-0.5 text-[10px] text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200 disabled:opacity-40"
            >
              {t("ask.community.force")}
            </button>
          </div>

          {naming.isError && (
            <p data-testid="ask-naming-error" className="mt-2 text-[11px] break-all text-rose-300">
              {askErrorMessage(naming.error)}
            </p>
          )}

          {naming.data && (
            <div data-testid="ask-naming-result" className="mt-2 text-[10px] text-zinc-400">
              <p data-testid="ask-naming-summary">
                {t("ask.community.updated", {
                  count: naming.data.updated,
                  source: naming.data.llm
                    ? t("ask.community.byLlm")
                    : t("ask.community.byHeuristic"),
                })}
              </p>
              {naming.data.errors.length > 0 && (
                <ul data-testid="ask-naming-errors" className="mt-1 list-disc pl-4 text-amber-400">
                  {naming.data.errors.map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                </ul>
              )}
              <ul className="mt-1 max-h-48 space-y-1 overflow-auto">
                {naming.data.communities.map((community) => (
                  <li
                    key={community.id}
                    data-testid="ask-naming-item"
                    data-community={community.id}
                    data-named-by={community.namedBy ?? ""}
                    className="rounded border border-zinc-800 px-1.5 py-1"
                  >
                    <div className="flex items-center gap-1">
                      <span className="truncate text-zinc-200">{community.name}</span>
                      <span
                        data-testid="ask-naming-by"
                        className={`shrink-0 rounded px-1 text-[9px] ${
                          community.namedBy === "llm"
                            ? "bg-sky-950 text-sky-300"
                            : "bg-zinc-800 text-zinc-400"
                        }`}
                      >
                        {community.namedBy ?? t("ask.community.unknown")}
                      </span>
                    </div>
                    {community.summary && <p className="mt-0.5 text-zinc-500">{community.summary}</p>}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <button
            type="button"
            data-testid="ask-summary"
            disabled={!repoId || summary.isPending}
            onClick={() => summary.mutate()}
            className="mt-3 rounded border border-zinc-700 px-2 py-0.5 text-[11px] text-zinc-200 hover:bg-zinc-800 disabled:opacity-40"
          >
            {summary.isPending ? t("ask.summary.generating") : t("ask.summary.button")}
          </button>

          {summary.isError && (
            <p data-testid="ask-summary-error" className="mt-2 text-[11px] break-all text-rose-300">
              {askErrorMessage(summary.error)}
            </p>
          )}

          {summary.data && (
            <div data-testid="ask-summary-result" className="mt-2 text-[10px]">
              <p data-testid="ask-summary-by" className="text-zinc-500">
                {t("ask.summary.source", {
                  source:
                    summary.data.generatedBy === "llm"
                      ? "LLM"
                      : t("ask.summary.sourceHeuristic"),
                })}
              </p>
              {summary.data.error && (
                <p data-testid="ask-summary-fallback" className="mt-0.5 text-amber-400">
                  {summary.data.error}
                </p>
              )}
              <div data-testid="ask-summary-text" className="mt-1 space-y-0.5">
                {summaryLines.map((line, index) => (
                  <p key={`${index}-${line}`} data-testid="ask-summary-line" className="leading-relaxed text-zinc-300">
                    {line}
                  </p>
                ))}
              </div>
            </div>
          )}
        </section>
      </aside>

      {/* 中栏：对话 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div data-testid="ask-messages" className="min-h-0 flex-1 space-y-3 overflow-auto px-4 py-3">
          {!repoId && (
            <p data-testid="ask-empty" className="text-xs leading-relaxed text-zinc-500">
              {t("ask.emptyNoRepo")}
            </p>
          )}
          {repoId && conversation.messages.length === 0 && (
            <p data-testid="ask-empty" className="text-xs leading-relaxed text-zinc-500">
              {t("ask.empty")}
            </p>
          )}
          {conversation.messages.map((message) => (
            <MessageRow key={message.id} message={message} onOpenCitation={openCitation} />
          ))}
        </div>

        <form
          className="shrink-0 border-t border-zinc-800 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            send(draft);
          }}
        >
          <textarea
            data-testid="ask-input"
            value={draft}
            disabled={inputDisabled}
            rows={3}
            placeholder={
              configured ? t("ask.placeholder") : t("ask.placeholderDisabled")
            }
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter" || event.shiftKey) return;
              // 中文输入法选词时的回车不算发送。
              if (event.nativeEvent.isComposing) return;
              event.preventDefault();
              send(draft);
            }}
            className="w-full resize-none rounded border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-[12px] text-zinc-100 focus:border-zinc-600 focus:outline-none disabled:opacity-40"
          />
          <div className="mt-2 flex items-center gap-2">
            <button
              type="submit"
              data-testid="ask-send"
              disabled={!canAsk || !draft.trim()}
              className="rounded border border-zinc-700 bg-zinc-900 px-3 py-1 text-[11px] text-zinc-100 hover:bg-zinc-800 disabled:opacity-40"
            >
              {busy ? t("ask.generating") : t("ask.send")}
            </button>
            {busy && (
              <button
                type="button"
                data-testid="ask-stop"
                onClick={stop}
                className="rounded border border-rose-900 px-2 py-1 text-[11px] text-rose-300 hover:bg-rose-950/40"
              >
                {t("ask.stop")}
              </button>
            )}
            <span className="text-[10px] text-zinc-600">{t("ask.inputHint")}</span>
          </div>
        </form>
      </div>

      {/* 右栏：引用与工具轨迹 */}
      <aside className="flex w-80 shrink-0 flex-col overflow-auto border-l border-zinc-800 bg-zinc-900/30">
        <section data-testid="ask-citations" className="border-b border-zinc-800 p-3">
          <h3 className="text-[10px] tracking-wide text-zinc-500 uppercase">
            {t("ask.citations")}{" "}
            <span data-testid="ask-citation-count" className="text-zinc-600">
              {citations.length}
            </span>
          </h3>
          {citations.length === 0 ? (
            <p data-testid="ask-citation-empty" className="mt-1 text-[11px] leading-relaxed text-zinc-500">
              {t("ask.citationsEmpty")}
            </p>
          ) : (
            <ul className="mt-1 flex flex-wrap gap-1">
              {citations.map((citation) => (
                <li key={citation.id}>
                  <CitationChip citation={citation} onOpen={openCitation} />
                </li>
              ))}
            </ul>
          )}
        </section>

        <section data-testid="ask-traces" className="p-3">
          <h3 className="text-[10px] tracking-wide text-zinc-500 uppercase">
            {t("ask.traces")}{" "}
            <span data-testid="ask-trace-count" className="text-zinc-600">
              {traces.length}
            </span>
          </h3>
          {traces.length === 0 ? (
            <p data-testid="ask-trace-empty" className="mt-1 text-[11px] leading-relaxed text-zinc-500">
              {t("ask.tracesEmpty")}
            </p>
          ) : (
            <ol data-testid="ask-trace-list" className="mt-1 space-y-1">
              {traces.map((trace, index) => (
                <li
                  key={`${trace.name}-${index}`}
                  data-testid="ask-trace-item"
                  data-tool={trace.name}
                  className="rounded border border-zinc-800 bg-zinc-950/60 p-2"
                >
                  <p className="font-mono text-[10px] text-zinc-300">{trace.name}</p>
                  <p className="mt-0.5 text-[10px] text-zinc-500">
                    {trace.summary ?? t("ask.toolPending")}
                  </p>
                  <details className="mt-1">
                    <summary className="cursor-pointer text-[10px] text-zinc-500">
                      {t("ask.traceParams")}
                    </summary>
                    <pre className="mt-0.5 overflow-auto font-mono text-[10px] text-zinc-500">
                      {JSON.stringify(trace.arguments)}
                    </pre>
                  </details>
                </li>
              ))}
            </ol>
          )}
        </section>
      </aside>
    </div>
  );
}
