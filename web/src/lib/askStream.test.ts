import { describe, expect, test } from "vitest";

import type { AskEvent } from "../api/ask";
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
} from "./askStream";

/** 走一遍「发送 → 一串事件」，返回最终状态。 */
function run(question: string, events: readonly AskEvent[]): AskConversation {
  return events.reduce(applyAskEvent, sendQuestion(emptyConversation, question));
}

const CITATION = { id: "py:a.py#f.function", name: "f", kind: "function", file: "a.py" };

describe("askStream 状态机", () => {
  test("sendQuestion 追加用户气泡与助手占位；空白问题忽略", () => {
    const state = sendQuestion(emptyConversation, "  谁是入口？  ");
    expect(state.messages).toHaveLength(2);
    expect(state.messages[0]).toMatchObject({ role: "user", content: "谁是入口？", status: "done" });
    expect(state.messages[1]).toMatchObject({ role: "assistant", content: "", status: "streaming" });
    expect(isStreaming(state)).toBe(true);
    expect(sendQuestion(state, "   ")).toBe(state);
    // id 递增，React key 不会撞
    expect(new Set(state.messages.map((m) => m.id)).size).toBe(2);
  });

  test("delta 逐段追加到占位消息上", () => {
    const state = run("q", [
      { type: "delta", text: "入口" },
      { type: "delta", text: "是 main.py" },
    ]);
    const assistant = state.messages[1];
    expect(assistant.content).toBe("入口是 main.py");
    expect(assistant.status).toBe("streaming");
  });

  test("空的 delta 不改状态（避免无意义重渲染）", () => {
    const started = sendQuestion(emptyConversation, "q");
    expect(applyAskEvent(started, { type: "delta", text: "" })).toBe(started);
  });

  test("tool / tool_result 配对成轨迹，summary 补在同名条目上", () => {
    const state = run("q", [
      { type: "tool", name: "search_symbols", arguments: { query: "Engine" } },
      { type: "tool", name: "get_symbol", arguments: { nodeId: "py:a.py#Engine.class" } },
      { type: "tool_result", name: "search_symbols", summary: "2 条匹配" },
      { type: "tool_result", name: "get_symbol", summary: "1 入边 / 3 出边" },
    ]);
    expect(state.messages[1].tools).toEqual([
      { name: "search_symbols", arguments: { query: "Engine" }, summary: "2 条匹配" },
      { name: "get_symbol", arguments: { nodeId: "py:a.py#Engine.class" }, summary: "1 入边 / 3 出边" },
    ]);
  });

  test("只有 tool_result 没有 tool 时也记一条（别丢信息）", () => {
    const state = run("q", [{ type: "tool_result", name: "impact", summary: "3 个文件" }]);
    expect(state.messages[1].tools).toEqual([{ name: "impact", arguments: {}, summary: "3 个文件" }]);
  });

  test("done：落最终回答、引用与终态", () => {
    const state = run("q", [
      { type: "delta", text: "入口是 " },
      { type: "done", answer: "入口是 main.py", citations: [CITATION] },
    ]);
    expect(state.messages[1]).toMatchObject({
      content: "入口是 main.py",
      status: "done",
      citations: [CITATION],
      error: null,
    });
    expect(isStreaming(state)).toBe(false);
  });

  test("done 的 answer 为空时保留已累积的 delta", () => {
    const state = run("q", [
      { type: "delta", text: "只说了一半" },
      { type: "done", answer: "", citations: [] },
    ]);
    expect(state.messages[1].content).toBe("只说了一半");
    expect(state.messages[1].status).toBe("done");
  });

  test("error：消息带后端给的原因并结束流", () => {
    const state = run("q", [
      { type: "delta", text: "开始" },
      { type: "error", message: "未配置 LLM（COGEN_LLM_API_KEY）" },
    ]);
    expect(state.messages[1]).toMatchObject({
      status: "error",
      error: "未配置 LLM（COGEN_LLM_API_KEY）",
      content: "开始",
    });
    expect(isStreaming(state)).toBe(false);
  });

  test("stopStreaming：标记 stopped 但保留已收到的内容", () => {
    const state = stopStreaming(run("q", [{ type: "delta", text: "半句" }]));
    expect(state.messages[1]).toMatchObject({ status: "stopped", content: "半句", error: null });
  });

  test("conversationHistory：跳过空助手占位，只取最后 limit 条", () => {
    const state = run("第一个问题", [{ type: "done", answer: "第一个回答", citations: [] }]);
    const next = run("第二个问题", [{ type: "error", message: "boom" }]);
    const merged: AskConversation = {
      messages: [...state.messages, ...next.messages],
      nextId: next.nextId,
    };
    // 最后一轮是 error：content 为空，不应作为上下文回传
    expect(conversationHistory(merged)).toEqual([
      { role: "user", content: "第一个问题" },
      { role: "assistant", content: "第一个回答" },
      { role: "user", content: "第二个问题" },
    ]);
    expect(conversationHistory(merged, 1)).toEqual([{ role: "user", content: "第二个问题" }]);
  });

  test("collectCitations 按 id 去重，collectToolTraces 按消息顺序摊平", () => {
    const state: AskConversation = {
      messages: [
        ...run("q1", [{ type: "delta", text: "a" }, { type: "tool", name: "t1", arguments: {} }]).messages,
        ...run("q2", [
          { type: "done", answer: "b", citations: [CITATION, { ...CITATION, name: "f2" }] },
        ]).messages,
      ],
      nextId: 5,
    };
    expect(collectCitations(state)).toEqual([CITATION]);
    expect(collectToolTraces(state).map((trace) => trace.name)).toEqual(["t1"]);
  });
});
