import { describe, expect, test, vi } from "vitest";

import {
  askPath,
  normalizeCitations,
  parseAskFrame,
  splitSseFrames,
  streamAsk,
  type AskHandlers,
} from "./ask";

/* -------------------------------------------------------------------------- */
/* 测试替身：可控的 SSE 响应体（不依赖 jsdom 是否有 ReadableStream）            */
/* -------------------------------------------------------------------------- */

/** 字符串 → UTF-8 字节（优先 TextEncoder，回退 Buffer）。 */
function encode(text: string): Uint8Array {
  if (typeof TextEncoder !== "undefined") return new TextEncoder().encode(text);
  return new Uint8Array(Buffer.from(text, "utf8"));
}

type Reader = {
  read: () => Promise<{ done: boolean; value?: Uint8Array }>;
  releaseLock: () => void;
  cancel: () => Promise<void>;
};

type FakeBody = { getReader: () => Reader; push: (text: string) => void; fail: (error: unknown) => void; close: () => void };

/** 手动驱动的响应体：测试决定每个 chunk 什么时候到达，以及流何时开始/结束/报错。 */
function makeBody(): FakeBody {
  const queue: string[] = [];
  let wake: (() => void) | null = null;
  let closed = false;
  let failure: unknown = null;

  const notify = () => {
    const resolve = wake;
    wake = null;
    resolve?.();
  };

  const read = async (): Promise<{ done: boolean; value?: Uint8Array }> => {
    for (;;) {
      if (failure) throw failure;
      if (queue.length > 0) return { done: false, value: encode(queue.shift() as string) };
      if (closed) return { done: true };
      await new Promise<void>((resolve) => {
        wake = resolve;
      });
    }
  };

  return {
    getReader: () => ({ read, releaseLock: () => {}, cancel: async () => {} }),
    push: (text) => {
      queue.push(text);
      notify();
    },
    fail: (error) => {
      failure = error;
      notify();
    },
    close: () => {
      closed = true;
      notify();
    },
  };
}

function jsonBody(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

/** 用一个 chunk 序列跑完整个流，收集回调顺序。 */
function collect() {
  const events: string[] = [];
  let finish!: () => void;
  const finished = new Promise<void>((resolve) => {
    finish = resolve;
  });
  const handlers: AskHandlers = {
    onTool: (call) => events.push(`tool:${call.name}:${JSON.stringify(call.arguments)}`),
    onToolResult: (result) => events.push(`result:${result.name}:${result.summary}`),
    onDelta: (text) => events.push(`delta:${text}`),
    onDone: (payload) =>
      events.push(`done:${payload.answer}:${payload.citations.map((c) => c.id).join("|")}`),
    onError: (message) => {
      events.push(`error:${message}`);
      finish();
    },
  };
  return {
    events,
    finished,
    handlers: {
      ...handlers,
      onDone: (payload: Parameters<NonNullable<AskHandlers["onDone"]>>[0]) => {
        handlers.onDone?.(payload);
        finish();
      },
    } satisfies AskHandlers,
  };
}

function frame(event: string, data: unknown, separator = "\n"): string {
  return `event: ${event}${separator}data: ${JSON.stringify(data)}${separator}${separator}`;
}

/** 让 reader/await 链上的微任务跑完（每轮 push 后都要等一拍）。 */
function tick(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

/* -------------------------------------------------------------------------- */

describe("SSE 分帧", () => {
  test("splitSseFrames 只切完整帧，半截留在 rest 里", () => {
    const { frames, rest } = splitSseFrames("event: a\ndata: 1\n\nevent: b\ndata: 2");
    expect(frames).toEqual(["event: a\ndata: 1"]);
    expect(rest).toBe("event: b\ndata: 2");
  });

  test("splitSseFrames 兼容 sse-starlette 的 \\r\\n，且不吞掉跨 chunk 的孤立 \\r", () => {
    // 第一段停在 \r\n\r 之后：这时候还不能算帧结束（下一 chunk 的 \n 才补齐）。
    const first = splitSseFrames("event: a\r\ndata: 1\r\n\r");
    expect(first.frames).toEqual([]);
    const second = splitSseFrames(`${first.rest}\n`);
    expect(second.frames).toEqual(["event: a\r\ndata: 1"]);
  });

  test("parseAskFrame 忽略注释/心跳行，未知事件返回 null", () => {
    expect(parseAskFrame(": ping")).toBeNull();
    expect(parseAskFrame("event: delta\ndata: not-json")).toBeNull();
    expect(parseAskFrame("event: something_else\ndata: {\"type\":\"nope\"}")).toBeNull();
    expect(parseAskFrame("event: delta\ndata: {\"type\":\"delta\",\"text\":\"嗨\"}")).toEqual({
      type: "delta",
      text: "嗨",
    });
  });

  test("normalizeCitations 丢掉没有 id 的条目", () => {
    expect(normalizeCitations([{ id: "a", name: "A" }, { name: "no-id" }, null, "x"])).toEqual([
      { id: "a", name: "A", kind: "symbol", file: null },
    ]);
    expect(normalizeCitations(undefined)).toEqual([]);
  });
});

describe("streamAsk", () => {
  const REPO = "repo 1";
  const URL = askPath(REPO);

  test("POST 到正确地址，带上 question 与 history", async () => {
    const { finished, handlers, events } = collect();
    const fetchMock = vi.fn(async () => jsonBody({}, 200));
    streamAsk(REPO, { question: "谁是入口", history: [{ role: "user", content: "hi" }] }, handlers, fetchMock as unknown as typeof fetch);

    // 没有 body → onError，但请求本身应当已经发出
    await finished;
    expect(URL).toBe("/api/repos/repo%201/ask");
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe(URL);
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["Content-Type"]).toBe("application/json");
    expect(JSON.parse(String(init.body))).toEqual({
      question: "谁是入口",
      history: [{ role: "user", content: "hi" }],
    });
    expect(events).toEqual(["error:后端没有返回事件流（缺少响应体）"]);
  });

  test("单个 chunk 里多个事件按顺序回调", async () => {
    const { events, finished, handlers } = collect();
    const body = makeBody();
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, body }) as unknown as Response);

    streamAsk("r", { question: "q", history: [] }, handlers, fetchMock as unknown as typeof fetch);

    body.push(
      frame("tool", { type: "tool", name: "search_symbols", arguments: { query: "Engine" } }) +
        frame("tool_result", { type: "tool_result", name: "search_symbols", summary: "3 条匹配" }) +
        frame("delta", { type: "delta", text: "入口是" }) +
        frame("done", {
          type: "done",
          answer: "入口是 main.py",
          citations: [{ id: "py:main.py#main.function", name: "main", kind: "function", file: "main.py" }],
        }),
    );
    body.close();

    await finished;
    expect(events).toEqual([
      'tool:search_symbols:{"query":"Engine"}',
      "result:search_symbols:3 条匹配",
      "delta:入口是",
      "done:入口是 main.py:py:main.py#main.function",
    ]);
  });

  test("事件被切成两个 chunk（拦腰截断 JSON）也能还原", async () => {
    const { events, finished, handlers } = collect();
    const body = makeBody();
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, body }) as unknown as Response);
    streamAsk("r", { question: "q", history: [] }, handlers, fetchMock as unknown as typeof fetch);

    const whole = frame("delta", { type: "delta", text: "完整回答" });
    const cut = Math.floor(whole.length / 2);
    body.push(whole.slice(0, cut));
    await tick();
    // 半截数据不该产生任何回调
    expect(events).toEqual([]);
    body.push(whole.slice(cut) + frame("done", { type: "done", answer: "完整回答", citations: [] }));
    body.close();

    await finished;
    expect(events).toEqual(["delta:完整回答", "done:完整回答:"]);
  });

  test("注释行（心跳）与 CRLF 帧不影响解析", async () => {
    const { events, finished, handlers } = collect();
    const body = makeBody();
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, body }) as unknown as Response);
    streamAsk("r", { question: "q", history: [] }, handlers, fetchMock as unknown as typeof fetch);

    body.push(": ping\r\n\r\n");
    body.push("event: delta\r\n: 备注\r\ndata: {\"type\":\"delta\",\"text\":\"a\"}\r\n\r\n");
    body.push(frame("done", { type: "done", answer: "a", citations: [] }));
    body.close();

    await finished;
    expect(events).toEqual(["delta:a", "done:a:"]);
  });

  test("非 2xx：把后端 detail 交给 onError", async () => {
    const { events, finished, handlers } = collect();
    const fetchMock = vi.fn(async () => jsonBody({ detail: "未配置 LLM" }, 503));
    streamAsk("r", { question: "q", history: [] }, handlers, fetchMock as unknown as typeof fetch);

    await finished;
    expect(events).toEqual(["error:未配置 LLM"]);
  });

  test("非 2xx 且响应不是 JSON：退化为状态码文案", async () => {
    const { events, finished, handlers } = collect();
    const fetchMock = vi.fn(async () => ({
      ok: false,
      status: 502,
      json: async () => {
        throw new Error("not json");
      },
    }) as unknown as Response);
    streamAsk("r", { question: "q", history: [] }, handlers, fetchMock as unknown as typeof fetch);

    await finished;
    expect(events).toEqual(["error:POST /api/repos/r/ask → HTTP 502"]);
  });

  test("网络异常（fetch reject）也走 onError", async () => {
    const { events, finished, handlers } = collect();
    const fetchMock = vi.fn(async () => {
      throw new Error("connection refused");
    });
    streamAsk("r", { question: "q", history: [] }, handlers, fetchMock as unknown as typeof fetch);

    await finished;
    expect(events).toEqual(["error:connection refused"]);
  });

  test("cancel() 中断 fetch，之后不再有任何回调（也不报错）", async () => {
    const { events, handlers } = collect();
    const body = makeBody();
    let signal: AbortSignal | undefined;
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      signal = init?.signal ?? undefined;
      signal?.addEventListener("abort", () => {
        body.fail(new Error("aborted"));
      });
      return { ok: true, status: 200, body } as unknown as Response;
    });

    const stream = streamAsk("r", { question: "q", history: [] }, handlers, fetchMock as unknown as typeof fetch);
    await tick();

    body.push(frame("delta", { type: "delta", text: "半句" }));
    await tick();
    expect(events).toEqual(["delta:半句"]);

    stream.cancel();
    expect(signal?.aborted).toBe(true);

    body.push(frame("delta", { type: "delta", text: "不该出现" }));
    body.close();
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(events).toEqual(["delta:半句"]);
  });
});
