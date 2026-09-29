/**
 * 「事件流 → 消息状态机」的纯逻辑：AskView 只负责渲染，这里不碰 React/fetch，
 * 因此流式的各种边界（delta 分段、工具轨迹配对、错误/停止）都能直接单测。
 */

import type { AskCitation, AskEvent, AskTurn, AskToolResult } from "../api/ask";

/** 一次工具调用的轨迹：tool 事件建条目，tool_result 事件补 summary。 */
export type AskToolTrace = {
  name: string;
  arguments: Record<string, unknown>;
  /** 结果摘要；null 表示还在等 tool_result。 */
  summary: string | null;
};

/** 消息状态：流式中 / 正常结束 / 用户点了停止 / 后端或连接报错。 */
export type ChatStatus = "streaming" | "done" | "stopped" | "error";

/** 一条对话消息（用户问题或助手回答）。 */
export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  status: ChatStatus;
  /** status === "error" 时的文案（后端 error 事件或流中断原因）。 */
  error: string | null;
  tools: AskToolTrace[];
  citations: AskCitation[];
};

export type AskConversation = {
  messages: ChatMessage[];
  /** 递增的消息 id 序号：让纯函数也保持确定性（不用随机数/时间戳）。 */
  nextId: number;
};

export const emptyConversation: AskConversation = { messages: [], nextId: 1 };

/** 回传给后端的历史轮数上限（后端最多收 20 条，这里留一点余量）。 */
export const HISTORY_LIMIT = 6;

/** 原位替换最后一条助手消息（保持不可变风格，便于 React 比较）。 */
function patchLastAssistant(
  state: AskConversation,
  patch: (message: ChatMessage) => ChatMessage,
): AskConversation {
  for (let i = state.messages.length - 1; i >= 0; i -= 1) {
    const message = state.messages[i];
    if (message.role !== "assistant") continue;
    const messages = [...state.messages];
    messages[i] = patch(message);
    return { ...state, messages };
  }
  return state;
}

/**
 * 用户发送：追加用户气泡 + 空的助手占位（状态 streaming）。
 * 先占位后追加 delta，流式渲染就是「往最后一条消息里贴字」。
 */
export function sendQuestion(state: AskConversation, question: string): AskConversation {
  const content = question.trim();
  if (!content) return state;
  const user: ChatMessage = {
    id: `m${state.nextId}`,
    role: "user",
    content,
    status: "done",
    error: null,
    tools: [],
    citations: [],
  };
  const assistant: ChatMessage = {
    id: `m${state.nextId + 1}`,
    role: "assistant",
    content: "",
    status: "streaming",
    error: null,
    tools: [],
    citations: [],
  };
  return { messages: [...state.messages, user, assistant], nextId: state.nextId + 2 };
}

/** tool_result 与 tool 事件配对：优先补最近一个同名且还没摘要的条目。 */
function attachToolResult(tools: AskToolTrace[], result: AskToolResult): AskToolTrace[] {
  for (let i = tools.length - 1; i >= 0; i -= 1) {
    const trace = tools[i];
    if (trace.summary === null && (trace.name === result.name || !result.name)) {
      const next = [...tools];
      next[i] = { ...trace, summary: result.summary };
      return next;
    }
  }
  // 只有 tool_result 没有 tool（异常路径）：仍然记下来，别丢信息。
  return [...tools, { name: result.name, arguments: {}, summary: result.summary }];
}

/** 消费一个事件，返回新状态；未知/无匹配消息时原样返回。 */
export function applyAskEvent(state: AskConversation, event: AskEvent): AskConversation {
  switch (event.type) {
    case "tool":
      return patchLastAssistant(state, (message) => ({
        ...message,
        tools: [...message.tools, { name: event.name, arguments: event.arguments, summary: null }],
      }));

    case "tool_result":
      return patchLastAssistant(state, (message) => ({
        ...message,
        tools: attachToolResult(message.tools, { name: event.name, summary: event.summary }),
      }));

    case "delta":
      if (!event.text) return state;
      return patchLastAssistant(state, (message) => ({
        ...message,
        content: message.content + event.text,
      }));

    case "done":
      return patchLastAssistant(state, (message) => ({
        ...message,
        // answer 是权威文本；为空（如只调工具没输出）时保留已累积的 delta。
        content: event.answer || message.content,
        status: "done",
        citations: event.citations,
      }));

    case "error":
      return patchLastAssistant(state, (message) => ({
        ...message,
        status: "error",
        error: event.message,
      }));
  }
}

/** 用户点了「停止」：保留已生成的内容，状态标成 stopped（不是错误）。 */
export function stopStreaming(state: AskConversation): AskConversation {
  return patchLastAssistant(state, (message) =>
    message.status === "streaming" ? { ...message, status: "stopped" } : message,
  );
}

/** 流被中断且后端没给原因时的兜底错误文案。 */
export function failStreaming(state: AskConversation, message: string): AskConversation {
  return applyAskEvent(state, { type: "error", message });
}

/** 是否还有消息在流式中（决定发送按钮禁用 / 停止按钮可见）。 */
export function isStreaming(state: AskConversation): boolean {
  return state.messages.some((message) => message.status === "streaming");
}

/**
 * 拼回多轮上下文：只带上真正有内容的问答（占位的空助手消息要排除），
 * 取最后 `limit` 条。
 */
export function conversationHistory(state: AskConversation, limit = HISTORY_LIMIT): AskTurn[] {
  const turns: AskTurn[] = [];
  for (const message of state.messages) {
    const content = message.content.trim();
    if (!content) continue;
    if (message.role === "user") turns.push({ role: "user", content: message.content });
    else if (message.status !== "streaming") turns.push({ role: "assistant", content: message.content });
  }
  return turns.slice(-limit);
}

/** 全部回答引用（按 id 去重，保持出现顺序）——右栏「引用」面板用。 */
export function collectCitations(state: AskConversation): AskCitation[] {
  const seen = new Set<string>();
  const out: AskCitation[] = [];
  for (const message of state.messages) {
    for (const citation of message.citations) {
      if (seen.has(citation.id)) continue;
      seen.add(citation.id);
      out.push(citation);
    }
  }
  return out;
}

/** 全部工具调用轨迹（按消息顺序摊平）——右栏「工具轨迹」面板用。 */
export function collectToolTraces(state: AskConversation): AskToolTrace[] {
  return state.messages.flatMap((message) => (message.role === "assistant" ? message.tools : []));
}
