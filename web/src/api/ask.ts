/**
 * M4 AI 接口：问答（POST + SSE 流）、LLM 状态、社区命名、架构摘要。
 *
 * 为什么问答不能复用 `api/events.ts` 的 EventSource 封装：
 *  - 问答是 **POST**（要带 question/history 与鉴权头），而 EventSource 只能 GET；
 *  - 因此这里用 `fetch` 拿到 `response.body` 流，自己按 SSE 规则分帧解析。
 *
 * 与 `events.ts` 保持一致的两点约定：
 *  - 事件流 → 回调（`AskHandlers`），组件不直接碰底层 reader；
 *  - 工厂可注入（`fetchImpl` 参数），jsdom 里用假实现就能覆盖跨 chunk 截断等边界。
 */

import { acceptLanguage, t } from "../i18n";
import { ApiError, requestJson } from "./client";

/** GET /ai/status：LLM 配置与可用工具（Key 本身不下发）。 */
export type AiStatus = {
  configured: boolean;
  model: string;
  baseUrl: string;
  /** 是否对送进模型的内容做脱敏。 */
  redact: boolean;
  /** 只读工具名清单（repo_overview / search_symbols / ...）。 */
  tools: string[];
  rag: string;
};

/** 可点击的引用（来自 done 事件 citations）。 */
export type AskCitation = {
  id: string;
  name: string;
  kind: string;
  file: string | null;
};

/** 一问一答（作为多轮上下文回传给后端）。 */
export type AskTurn = { role: "user" | "assistant"; content: string };

/** POST /ask 的请求体。 */
export type AskRequest = { question: string; history: AskTurn[] };

/** 一次工具调用（tool 事件）。 */
export type AskToolCall = { name: string; arguments: Record<string, unknown> };

/** 一次工具调用结果（tool_result 事件）。 */
export type AskToolResult = { name: string; summary: string };

/** done 事件载荷：最终回答 + 引用。 */
export type AskDonePayload = { answer: string; citations: AskCitation[] };

/** 流式回调；任何一个都可以不传。 */
export type AskHandlers = {
  onTool?: (call: AskToolCall) => void;
  onToolResult?: (result: AskToolResult) => void;
  /** 最终回答的文本增量。 */
  onDelta?: (text: string) => void;
  onDone?: (payload: AskDonePayload) => void;
  onError?: (message: string) => void;
};

/** 归一化后的事件（`lib/askStream.ts` 的状态机就吃这个）。 */
export type AskEvent =
  | ({ type: "tool" } & AskToolCall)
  | ({ type: "tool_result" } & AskToolResult)
  | { type: "delta"; text: string }
  | ({ type: "done" } & AskDonePayload)
  | { type: "error"; message: string };

/** 订阅句柄：cancel 后不再有任何回调（UI 切换/停止/卸载时调用）。 */
export type AskStream = { cancel: () => void };

/** 社区命名结果里的单个社区。 */
export type NamedCommunity = {
  id: number;
  name: string;
  summary: string | null;
  namedBy: "llm" | "heuristic" | null;
};

/** POST /communities/name 的响应。 */
export type CommunityNamingResult = {
  updated: number;
  /** 本次是否真的调用了 LLM（未配置时为 false，名称来自确定性启发式）。 */
  llm: boolean;
  errors: string[];
  communities: NamedCommunity[];
};

/** GET /summary 的响应。 */
export type ArchitectureSummary = {
  summary: string;
  generatedBy: "llm" | "heuristic";
  /** 调用 LLM 失败时的原因（此时 summary 是启发式兜底文本）。 */
  error?: string;
};

/** 问答接口路径（集中一处，测试也直接引用，避免手写字符串拼错）。 */
export function askPath(repoId: string): string {
  return `/api/repos/${encodeURIComponent(repoId)}/ask`;
}

/** 逐帧切分 SSE 缓冲区；返回「完整帧」与需要留到下一 chunk 的尾巴。 */
export function splitSseFrames(buffer: string): { frames: string[]; rest: string } {
  const frames: string[] = [];
  let rest = buffer;
  // 用正则而不是 replace：跨 chunk 结尾的孤立 `\r` 必须原样留到下一轮，
  // 否则会被误判成空行，把半截事件切坏（sse-starlette 默认用 \r\n）。
  const separator = /\r?\n\r?\n/;
  for (;;) {
    const match = separator.exec(rest);
    if (!match) break;
    frames.push(rest.slice(0, match.index));
    rest = rest.slice(match.index + match[0].length);
  }
  return { frames, rest };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

/** 引用列表归一化：字段缺失的条目直接丢掉，避免渲染出空 chip。 */
export function normalizeCitations(raw: unknown): AskCitation[] {
  if (!Array.isArray(raw)) return [];
  const out: AskCitation[] = [];
  for (const item of raw) {
    if (!isRecord(item)) continue;
    const id = text(item.id);
    if (!id) continue;
    const file = text(item.file);
    out.push({ id, name: text(item.name) || id, kind: text(item.kind) || "symbol", file: file || null });
  }
  return out;
}

/** 把一条 SSE 帧（不含结尾空行）解析成事件；注释/心跳/未知事件返回 null。 */
export function parseAskFrame(frame: string): AskEvent | null {
  let eventName = "message";
  const dataLines: string[] = [];

  for (const line of frame.split(/\r?\n/)) {
    // 空行是帧内分隔；以 `:` 开头的是注释（sse-starlette 的 ping 心跳）。
    if (!line || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") eventName = value.trim();
    else if (field === "data") dataLines.push(value);
  }

  if (dataLines.length === 0) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(dataLines.join("\n"));
  } catch {
    // 半截 JSON（不应出现，因为分帧已保证完整）：忽略而不是让整条流挂掉。
    return null;
  }
  return toAskEvent(eventName, raw);
}

/**
 * 事件名与 `data.type` 二者取其一：优先信 data.type（后端把它写在载荷里），
 * 缺失时退回 SSE 的 event 名。
 */
function toAskEvent(eventName: string, raw: unknown): AskEvent | null {
  if (!isRecord(raw)) return null;
  const type = text(raw.type) || eventName;
  switch (type) {
    case "tool":
      return {
        type: "tool",
        name: text(raw.name),
        arguments: isRecord(raw.arguments) ? raw.arguments : {},
      };
    case "tool_result":
      return { type: "tool_result", name: text(raw.name), summary: text(raw.summary) };
    case "delta":
      return { type: "delta", text: text(raw.text) };
    case "done":
      return {
        type: "done",
        answer: text(raw.answer),
        citations: normalizeCitations(raw.citations),
      };
    case "error":
      return { type: "error", message: text(raw.message) || t("askApi.failed") };
    default:
      return null;
  }
}

function dispatch(event: AskEvent, handlers: AskHandlers): void {
  switch (event.type) {
    case "tool":
      handlers.onTool?.({ name: event.name, arguments: event.arguments });
      break;
    case "tool_result":
      handlers.onToolResult?.({ name: event.name, summary: event.summary });
      break;
    case "delta":
      handlers.onDelta?.(event.text);
      break;
    case "done":
      handlers.onDone?.({ answer: event.answer, citations: event.citations });
      break;
    case "error":
      handlers.onError?.(event.message);
      break;
  }
}

/** 非 2xx 的可读原因：优先后端 detail，退化为状态码。 */
async function streamErrorMessage(res: Response, url: string): Promise<string> {
  try {
    const data = (await res.json()) as { detail?: unknown } | null;
    const detail = data?.detail;
    if (typeof detail === "string" && detail.trim()) return detail;
  } catch {
    // 不是 JSON（网关页/被 abort）：退化为状态码文案。
  }
  return `POST ${url} → HTTP ${res.status}`;
}

/**
 * 发起一次问答并消费 SSE 流。
 *
 * @returns `{ cancel() }`：中断 fetch 且保证之后不再触发任何回调。
 * 断流、非 2xx、缺少 body 都走 `onError`，UI 只需处理这一条错误通道。
 */
export function streamAsk(
  repoId: string,
  body: AskRequest,
  handlers: AskHandlers,
  fetchImpl?: typeof fetch,
): AskStream {
  const url = askPath(repoId);
  const doFetch = fetchImpl ?? fetch;
  const controller = new AbortController();
  let cancelled = false;

  const emit = (event: AskEvent) => {
    if (cancelled) return;
    dispatch(event, handlers);
  };

  const consume = async () => {
    const res = await doFetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        "Accept-Language": acceptLanguage(),
      },
      body: JSON.stringify(body),
      signal: controller.signal,
    });

    if (!res.ok) {
      emit({ type: "error", message: await streamErrorMessage(res, url) });
      return;
    }
    if (!res.body) {
      emit({ type: "error", message: t("askApi.noBody") });
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const { frames, rest } = splitSseFrames(buffer);
        buffer = rest;
        for (const frame of frames) {
          const event = parseAskFrame(frame);
          if (event) emit(event);
        }
      }
      // 流结束时可能没有结尾空行：把剩下的半帧按完整帧再试一次。
      buffer += decoder.decode();
      for (const frame of splitSseFrames(`${buffer}\n\n`).frames) {
        const event = parseAskFrame(frame);
        if (event) emit(event);
      }
    } finally {
      try {
        reader.releaseLock();
      } catch {
        // 流已被 abort 取消时 releaseLock 会抛；这里不需要处理。
      }
    }
  };

  void consume().catch((error: unknown) => {
    // 主动取消导致的 AbortError 不是错误，不该在对话里留下红字。
    if (cancelled || controller.signal.aborted) return;
    emit({ type: "error", message: error instanceof Error ? error.message : String(error) });
  });

  return {
    cancel() {
      if (cancelled) return;
      cancelled = true;
      controller.abort();
    },
  };
}

/** 把后端的字段缺失兜底成 UI 可信的结构。 */
function normalizeStatus(raw: unknown): AiStatus {
  const data = isRecord(raw) ? raw : {};
  return {
    configured: data.configured === true,
    model: text(data.model),
    baseUrl: text(data.baseUrl),
    redact: data.redact === true,
    tools: Array.isArray(data.tools) ? data.tools.filter((t): t is string => typeof t === "string") : [],
    rag: text(data.rag) || "graph-tools",
  };
}

/** LLM 配置与工具清单（未配置也要能拿到，UI 据此提示）。 */
export async function getAiStatus(repoId: string, signal?: AbortSignal): Promise<AiStatus> {
  const raw = await requestJson<unknown>(`/api/repos/${encodeURIComponent(repoId)}/ai/status`, {
    signal,
  });
  return normalizeStatus(raw);
}

function normalizeNaming(raw: unknown): CommunityNamingResult {
  const data = isRecord(raw) ? raw : {};
  const communities = Array.isArray(data.communities) ? data.communities : [];
  return {
    updated: typeof data.updated === "number" ? data.updated : 0,
    llm: data.llm === true,
    errors: Array.isArray(data.errors) ? data.errors.filter((e): e is string => typeof e === "string") : [],
    communities: communities.filter(isRecord).map((item) => {
      const namedBy = item.namedBy;
      const summary = text(item.summary);
      return {
        id: typeof item.id === "number" ? item.id : -1,
        name: text(item.name),
        summary: summary || null,
        namedBy: namedBy === "llm" || namedBy === "heuristic" ? namedBy : null,
      };
    }),
  };
}

/**
 * 让后端为社区生成名称/摘要：未配置 LLM 时后端回落到确定性启发式，接口不会失败，
 * 因此 UI 不需要禁用按钮，只看结果里的 `llm` 与每个社区的 `namedBy`。
 */
export async function nameCommunities(
  repoId: string,
  force = false,
  signal?: AbortSignal,
): Promise<CommunityNamingResult> {
  const search = new URLSearchParams({ force: force ? "true" : "false" });
  const raw = await requestJson<unknown>(
    `/api/repos/${encodeURIComponent(repoId)}/communities/name?${search.toString()}`,
    { method: "POST", signal },
  );
  return normalizeNaming(raw);
}

/** 架构摘要：LLM 可用时用 LLM，否则给启发式要点（`generatedBy` 标明来源）。 */
export async function getSummary(repoId: string, signal?: AbortSignal): Promise<ArchitectureSummary> {
  const raw = await requestJson<unknown>(`/api/repos/${encodeURIComponent(repoId)}/summary`, {
    signal,
  });
  const data = isRecord(raw) ? raw : {};
  const error = text(data.error);
  return {
    summary: text(data.summary),
    generatedBy: data.generatedBy === "llm" ? "llm" : "heuristic",
    ...(error ? { error } : {}),
  };
}

/** 便于 UI 统一展示接口错误（与 client.ts 同一套 ApiError）。 */
export function askErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return error.message;
  return String(error);
}
