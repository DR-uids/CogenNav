import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import App from "../App";
import type { GraphNode } from "../api/client";
import {
  resetGraphRenderers,
  setGraphRenderers,
  type DagRendererProps,
  type ForceRendererProps,
} from "../components/graph/renderers";
import { useUi } from "../stores/ui";
import { AskView } from "./AskView";

// 真实渲染器在 jsdom 里跑不起来：这里既注入替身，也把 lazy 模块替换成空组件兜底。
vi.mock("../components/graph/ForceGraph", () => ({ default: () => null }));
vi.mock("../components/graph/LayeredDag", () => ({ default: () => null }));

const REPO = "repo-1";
const CITATION = {
  id: "python:src/main.py#main.function",
  name: "main",
  kind: "function",
  file: "src/main.py",
};

const HEALTH = {
  status: "ok",
  version: "0.1.0",
  home: "/tmp/cogen",
  llm_configured: true,
  web_built: true,
};

const AI_STATUS = {
  configured: true,
  model: "deepseek-chat",
  baseUrl: "https://api.deepseek.com/v1",
  redact: true,
  tools: ["repo_overview", "search_symbols", "get_symbol", "impact"],
  rag: "graph-tools",
};

const NAMING = {
  updated: 2,
  llm: false,
  errors: [],
  communities: [
    { id: 1, name: "认证", summary: "登录与令牌校验", namedBy: "heuristic" },
    { id: 2, name: "存储", summary: null, namedBy: "heuristic" },
  ],
};

const SUMMARY = {
  summary: "仓库：demo\n- 分为认证与存储两块\n- 核心抽象：Engine",
  generatedBy: "heuristic",
};

const NODES: GraphNode[] = [
  {
    id: CITATION.id,
    kind: "function",
    name: "main",
    qualified: "main",
    file: "src/main.py",
    language: "python",
    community: 1,
    degree: 3,
    inDegree: 1,
    outDegree: 2,
    start: [0, 0],
  },
];

const ANALYSIS = {
  communities: [{ id: 1, name: "认证", size: 1, cohesion: null, namedBy: "heuristic", topSymbols: [] }],
  godNodes: [{ id: CITATION.id, name: "main", kind: "function", file: "src/main.py", degree: 3, inDegree: 1, outDegree: 2 }],
  cycles: [],
  orphans: [],
  stats: {
    nodes: 1,
    edges: 0,
    byKind: { function: 1 },
    byRelation: {},
    byConfidence: {},
    resolvedCallRate: null,
  },
};

/* -------------------------------------------------------------------------- */
/* 测试替身                                                                    */
/* -------------------------------------------------------------------------- */

function jsonRes(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response;
}

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

type FakeBody = { getReader: () => Reader; push: (text: string) => void; close: () => void };

/** 手动驱动的 SSE 响应体：测试决定每个 chunk 何时到达。 */
function makeBody(): FakeBody {
  const queue: string[] = [];
  let wake: (() => void) | null = null;
  let closed = false;

  const read = async (): Promise<{ done: boolean; value?: Uint8Array }> => {
    for (;;) {
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
      const resolve = wake;
      wake = null;
      resolve?.();
    },
    close: () => {
      closed = true;
      const resolve = wake;
      wake = null;
      resolve?.();
    },
  };
}

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

/** 手动推一个 chunk 并等 React 落地。 */
async function push(body: FakeBody, text: string) {
  await act(async () => {
    body.push(text);
    await tick();
  });
}

type FetchOptions = {
  status?: unknown;
  naming?: unknown;
  summary?: unknown;
  askError?: { body: unknown; status: number };
};

function installFetch(options: FetchOptions = {}) {
  const calls: { url: string; init?: RequestInit }[] = [];
  const streams: FakeBody[] = [];

  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push({ url, init });
    const path = new URL(url, "http://localhost").pathname;

    if (path === "/api/health") return jsonRes(HEALTH);
    if (path === "/api/repos") return jsonRes({ repos: [] });
    if (path.endsWith("/ai/status")) return jsonRes(options.status ?? AI_STATUS);
    if (path.endsWith("/communities/name")) return jsonRes(options.naming ?? NAMING);
    if (path.endsWith("/summary")) return jsonRes(options.summary ?? SUMMARY);
    if (path.endsWith("/ask")) {
      if (options.askError) return jsonRes(options.askError.body, options.askError.status);
      const body = makeBody();
      streams.push(body);
      return { ok: true, status: 200, body } as unknown as Response;
    }
    if (path.endsWith("/analysis")) return jsonRes(ANALYSIS);
    if (path.endsWith("/graph")) {
      return jsonRes({ nodes: NODES, edges: [], total: { nodes: 1, edges: 0 }, truncated: false });
    }
    if (path.endsWith("/symbol")) {
      return jsonRes({ node: NODES[0], incoming: [], outgoing: [], community: null, definition: null });
    }
    if (path.endsWith("/tree")) {
      return jsonRes({ path: "", node: null, totalLoc: 0, totalFiles: 0 });
    }
    if (path.endsWith("/files")) return jsonRes({ total: 0, files: [] });
    return jsonRes({});
  });

  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls, streams };
}

/** 取某类接口的请求（按 path 后缀过滤）。 */
function callsOf(calls: readonly { url: string; init?: RequestInit }[], suffix: string) {
  return calls.filter((call) => new URL(call.url, "http://localhost").pathname.endsWith(suffix));
}

function ForceSpy({ nodes, selectedId, onSelectNode }: ForceRendererProps) {
  return (
    <div data-testid="force-renderer" data-nodes={nodes.length} data-selected={selectedId ?? ""}>
      {nodes.map((item) => (
        <button key={item.id} type="button" data-testid="renderer-node" data-id={item.id} onClick={() => onSelectNode(item.id)}>
          {item.name}
        </button>
      ))}
    </div>
  );
}

function DagSpy({ selectedId }: DagRendererProps) {
  return <div data-testid="dag-renderer" data-selected={selectedId ?? ""} />;
}

/* -------------------------------------------------------------------------- */

function renderAsk(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const view = render(
    <QueryClientProvider client={client}>
      <AskView />
    </QueryClientProvider>,
  );
  return { client, ...view };
}

function renderApp(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const view = render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
  return { client, ...view };
}

/** 取最后一条助手消息（用户消息也带 ask-message-content，断言必须限定范围）。 */
function lastAssistantMessage(): HTMLElement {
  const messages = screen.getAllByTestId("ask-message");
  const assistant = messages.filter((item) => item.getAttribute("data-role") === "assistant").at(-1);
  if (!assistant) throw new Error("没有助手消息");
  return assistant;
}

/** 等 AI 状态落地（发送按钮/输入框此时才可用）。 */
async function waitForStatus() {
  await screen.findByTestId("ask-status-model");
}

beforeEach(() => {
  resetGraphRenderers();
  setGraphRenderers({ force: ForceSpy, dag: DagSpy });
  useUi.setState({
    activeView: "ask",
    repoId: REPO,
    jobId: null,
    jobProgress: null,
    selectedFile: null,
    selectedNode: null,
    selectionOrigin: null,
  });
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("AskView 对话", () => {
  test("Enter 发送：用户消息出现，delta 分段渲染，done 后出现 citations", async () => {
    const { calls, streams } = installFetch();
    renderAsk();
    await waitForStatus();

    const input = screen.getByTestId("ask-input") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "入口在哪个文件？" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(callsOf(calls, "/ask")).toHaveLength(1));

    // 请求体契约
    const sent = JSON.parse(String(callsOf(calls, "/ask")[0].init?.body));
    expect(sent).toEqual({ question: "入口在哪个文件？", history: [] });
    expect(callsOf(calls, "/ask")[0].init?.method).toBe("POST");
    expect((callsOf(calls, "/ask")[0].init?.headers as Record<string, string>).Accept).toBe(
      "text/event-stream",
    );

    const user = await screen.findByText("入口在哪个文件？");
    expect(user.closest('[data-testid="ask-message"]')?.getAttribute("data-role")).toBe("user");
    expect(input.value).toBe("");

    const stream = streams[0];
    await push(stream, frame("delta", { type: "delta", text: "入口是" }));
    await waitFor(() =>
      expect(within(lastAssistantMessage()).getByTestId("ask-message-content").textContent).toContain(
        "入口是",
      ),
    );

    await push(stream, frame("delta", { type: "delta", text: " src/main.py 的 main()" }));
    await waitFor(() =>
      expect(within(lastAssistantMessage()).getByTestId("ask-message-content").textContent).toContain(
        "入口是 src/main.py 的 main()",
      ),
    );
    // 还没 done：会话里没有引用
    expect(screen.getByTestId("ask-citation-empty")).toBeTruthy();

    await push(stream, frame("done", { type: "done", answer: "入口是 src/main.py 的 main()", citations: [CITATION] }));

    // done 之后：消息内引用 chip + 右栏汇总
    const chip = within(screen.getByTestId("ask-message-citations")).getByTestId("ask-citation");
    expect(chip.getAttribute("data-id")).toBe(CITATION.id);
    expect(chip.getAttribute("data-kind")).toBe("function");
    expect(chip.textContent).toContain("main");
    expect(screen.getByTestId("ask-citation-count").textContent).toBe("1");
    expect(screen.queryByTestId("ask-stop")).toBeNull();
  });

  test("Shift+Enter 换行不发送；空白问题点发送也不发送", async () => {
    const { calls } = installFetch();
    renderAsk();
    await waitForStatus();

    const input = screen.getByTestId("ask-input");
    fireEvent.change(input, { target: { value: "第一行" } });
    fireEvent.keyDown(input, { key: "Enter", shiftKey: true });
    expect(callsOf(calls, "/ask")).toHaveLength(0);

    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.click(screen.getByTestId("ask-send"));
    expect(callsOf(calls, "/ask")).toHaveLength(0);
  });

  test("点引用：selectNode(id, graph) + 切到图谱视图", async () => {
    const { streams } = installFetch();
    renderAsk();
    await waitForStatus();

    const input = screen.getByTestId("ask-input");
    fireEvent.change(input, { target: { value: "谁是入口" } });
    fireEvent.click(screen.getByTestId("ask-send"));
    await waitFor(() => expect(streams).toHaveLength(1));

    await push(streams[0], frame("done", { type: "done", answer: "main", citations: [CITATION] }));

    fireEvent.click(within(screen.getByTestId("ask-message-citations")).getByTestId("ask-citation"));

    expect(useUi.getState().selectedNode).toBe(CITATION.id);
    expect(useUi.getState().selectionOrigin).toBe("graph");
    expect(useUi.getState().activeView).toBe("graph");
  });

  test("停止：中断后续渲染并标记已停止", async () => {
    const { streams } = installFetch();
    renderAsk();
    await waitForStatus();

    fireEvent.change(screen.getByTestId("ask-input"), { target: { value: "问" } });
    fireEvent.click(screen.getByTestId("ask-send"));
    await waitFor(() => expect(streams).toHaveLength(1));

    await push(streams[0], frame("delta", { type: "delta", text: "半句" }));
    fireEvent.click(screen.getByTestId("ask-stop"));

    expect(screen.getByTestId("ask-message-stopped")).toBeTruthy();
    // 停止后到达的 delta 不再渲染
    await push(streams[0], frame("delta", { type: "delta", text: "不该出现" }));
    const content = within(lastAssistantMessage()).getByTestId("ask-message-content");
    expect(content.textContent).toContain("半句");
    expect(content.textContent).not.toContain("不该出现");
  });

  test("错误事件：消息里显示后端给的 message", async () => {
    const { streams } = installFetch();
    renderAsk();
    await waitForStatus();

    fireEvent.change(screen.getByTestId("ask-input"), { target: { value: "问" } });
    fireEvent.click(screen.getByTestId("ask-send"));
    await waitFor(() => expect(streams).toHaveLength(1));

    await push(
      streams[0],
      frame("error", { type: "error", message: "未配置 LLM（COGEN_LLM_API_KEY），问答不可用。" }),
    );

    const error = await screen.findByTestId("ask-message-error");
    expect(error.textContent).toContain("未配置 LLM（COGEN_LLM_API_KEY），问答不可用。");
    expect(lastAssistantMessage().getAttribute("data-status")).toBe("error");
  });

  test("工具轨迹：默认折叠，展开后能看到参数与 tool_result.summary", async () => {
    const { streams } = installFetch();
    renderAsk();
    await waitForStatus();

    fireEvent.change(screen.getByTestId("ask-input"), { target: { value: "谁调用了 Engine.run" } });
    fireEvent.click(screen.getByTestId("ask-send"));
    await waitFor(() => expect(streams).toHaveLength(1));

    const stream = streams[0];
    await push(stream, frame("tool", { type: "tool", name: "search_symbols", arguments: { query: "Engine" } }));
    await push(stream, frame("tool_result", { type: "tool_result", name: "search_symbols", summary: "3 条匹配" }));
    await push(stream, frame("tool", { type: "tool", name: "impact", arguments: { nodeId: "py:a.py#Engine.run.method" } }));
    await push(stream, frame("tool_result", { type: "tool_result", name: "impact", summary: "需要回归 2 个文件" }));
    await push(stream, frame("done", { type: "done", answer: "答案是 …", citations: [] }));

    const toggle = screen.getByTestId("ask-tools-toggle");
    expect(toggle.textContent).toContain("2 tool calls");
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByTestId("ask-tool-list")).toBeNull();

    fireEvent.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");

    const items = screen.getAllByTestId("ask-tool-item");
    expect(items).toHaveLength(2);
    expect(items[0].getAttribute("data-tool")).toBe("search_symbols");
    expect(within(items[0]).getByTestId("ask-tool-args").textContent).toBe('{"query":"Engine"}');
    expect(within(items[0]).getByTestId("ask-tool-summary").textContent).toBe("3 条匹配");
    expect(within(items[1]).getByTestId("ask-tool-args").textContent).toContain("Engine.run.method");

    // 右栏也汇总了工具轨迹
    expect(screen.getByTestId("ask-trace-count").textContent).toBe("2");
    expect(screen.getAllByTestId("ask-trace-item")).toHaveLength(2);
  });

  test("未配置 LLM：给出明确提示并禁用输入与发送", async () => {
    installFetch({
      status: { ...AI_STATUS, configured: false, model: "" },
    });
    renderAsk();

    const warning = await screen.findByTestId("ask-llm-warning");
    expect(warning.textContent).toContain("COGEN_LLM_API_KEY");
    expect(warning.textContent).toContain("are unaffected");

    expect((screen.getByTestId("ask-input") as HTMLTextAreaElement).disabled).toBe(true);
    expect((screen.getByTestId("ask-send") as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getAllByTestId("ask-quick-item")[0] as HTMLButtonElement).disabled).toBe(true);
    // 历史消息区仍然可读（空态文案在）
    expect(screen.getByTestId("ask-empty")).toBeTruthy();
    // 社区命名不依赖 LLM，仍然可点
    expect((screen.getByTestId("ask-name-communities") as HTMLButtonElement).disabled).toBe(false);
  });

  test("快捷问题：点击直接发送", async () => {
    const { calls } = installFetch();
    renderAsk();
    await waitForStatus();

    fireEvent.click(screen.getAllByTestId("ask-quick-item")[1]);
    await waitFor(() => expect(callsOf(calls, "/ask")).toHaveLength(1));
    expect(JSON.parse(String(callsOf(calls, "/ask")[0].init?.body)).question).toBe("Who calls Engine.run?");
  });
});

describe("AskView 社区命名与架构摘要", () => {
  test("社区命名：POST 正确 URL，成功后失效 analysis/graph 等 query", async () => {
    const { calls } = installFetch();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidateSpy = vi.spyOn(client, "invalidateQueries");
    renderAsk(client);
    await waitForStatus();

    fireEvent.click(screen.getByTestId("ask-name-communities"));

    await waitFor(() => expect(callsOf(calls, "/communities/name")).toHaveLength(1));
    const request = callsOf(calls, "/communities/name")[0];
    expect(request.url).toBe(`/api/repos/${REPO}/communities/name?force=false`);
    expect(request.init?.method).toBe("POST");

    // 结果落地：updated / namedBy 都展示出来
    const result = await screen.findByTestId("ask-naming-result");
    expect(result.textContent).toContain("Updated 2 communities");
    expect(result.textContent).toContain("named heuristically");
    const items = screen.getAllByTestId("ask-naming-item");
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByTestId("ask-naming-by").textContent).toBe("heuristic");

    // 社区名写回图谱：analysis 与 graph 相关 query 被失效
    const keys = invalidateSpy.mock.calls.map(
      (call) => (call[0] as { queryKey: unknown[] }).queryKey,
    );
    expect(keys).toContainEqual(["analysis", REPO]);
    expect(keys).toContainEqual(["graph", REPO]);
    expect(keys).toContainEqual(["symbol", REPO]);

    // 「强制重新命名」走 force=true
    fireEvent.click(screen.getByTestId("ask-name-communities-force"));
    await waitFor(() => {
      const all = callsOf(calls, "/communities/name");
      expect(all).toHaveLength(2);
      expect(all[1].url).toBe(`/api/repos/${REPO}/communities/name?force=true`);
    });
  });

  test("架构摘要：按行渲染并标注 generatedBy", async () => {
    const { calls } = installFetch();
    renderAsk();
    await waitForStatus();

    fireEvent.click(screen.getByTestId("ask-summary"));
    await waitFor(() => expect(callsOf(calls, "/summary")).toHaveLength(1));

    const result = await screen.findByTestId("ask-summary-result");
    expect(within(result).getByTestId("ask-summary-by").textContent).toContain("heuristic");
    const lines = within(result).getAllByTestId("ask-summary-line");
    expect(lines.map((line) => line.textContent)).toEqual([
      "仓库：demo",
      "- 分为认证与存储两块",
      "- 核心抽象：Engine",
    ]);
  });
});

describe("AskView 引用跳转到图谱（跨视图联动）", () => {
  test("点引用后 GraphView 切到该符号并聚焦", async () => {
    const { calls, streams } = installFetch();
    renderApp();

    expect(await screen.findByTestId("view-ask")).toBeTruthy();
    await waitForStatus();

    fireEvent.change(screen.getByTestId("ask-input"), { target: { value: "入口" } });
    fireEvent.click(screen.getByTestId("ask-send"));
    await waitFor(() => expect(streams).toHaveLength(1));
    await push(streams[0], frame("done", { type: "done", answer: "main", citations: [CITATION] }));

    fireEvent.click(within(screen.getByTestId("ask-message-citations")).getByTestId("ask-citation"));

    // 视图切到图谱
    expect(await screen.findByTestId("view-graph")).toBeTruthy();
    expect(useUi.getState().activeView).toBe("graph");
    expect(useUi.getState().selectedNode).toBe(CITATION.id);

    // GraphView 响应 store 里的选中：以该符号为焦点取邻域，并把它交给渲染器选中
    await waitFor(() => {
      const graphCalls = callsOf(calls, "/graph");
      expect(graphCalls.length).toBeGreaterThan(0);
      const params = new URL(graphCalls.at(-1)?.url ?? "", "http://localhost").searchParams;
      expect(params.get("focus")).toBe(CITATION.id);
    });
    await waitFor(() =>
      expect(screen.getByTestId("force-renderer").getAttribute("data-selected")).toBe(CITATION.id),
    );
    // 深链也带上了该节点
    expect(window.location.search).toContain(`node=${encodeURIComponent(CITATION.id)}`);
  });
});
