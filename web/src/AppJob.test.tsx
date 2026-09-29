import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import App from "./App";
import { setJobEventsFactory, type EventSourceLike, type SseEvent } from "./api/events";
import { useUi } from "./stores/ui";

const HEALTH = {
  status: "ok",
  version: "0.1.0",
  home: "/tmp/cogen",
  llm_configured: false,
  web_built: false,
};

function jsonRes(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

class FakeEventSource implements EventSourceLike {
  readonly url: string;
  closed = false;
  private readonly listeners = new Map<string, ((ev: SseEvent) => void)[]>();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, listener: (ev: SseEvent) => void): void {
    const list = this.listeners.get(type) ?? [];
    list.push(listener);
    this.listeners.set(type, list);
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string, data: unknown): void {
    const payload = typeof data === "string" ? data : JSON.stringify(data);
    for (const listener of this.listeners.get(type) ?? []) listener({ data: payload });
  }
}

beforeEach(() => {
  useUi.setState({ activeView: "tree", repoId: null, jobId: null, jobProgress: null });
});

afterEach(() => {
  setJobEventsFactory(null);
  vi.unstubAllGlobals();
});

describe("App 与索引任务 SSE 的接线", () => {
  test("有 jobId 时订阅 SSE：progress 落到 store，done 关闭连接并刷新列表", async () => {
    const created: FakeEventSource[] = [];
    setJobEventsFactory((url) => {
      const source = new FakeEventSource(url);
      created.push(source);
      return source;
    });

    const fetchMock = vi.fn(async (url: string) =>
      url === "/api/health" ? jsonRes(HEALTH) : jsonRes({ repos: [] }),
    );
    vi.stubGlobal("fetch", fetchMock);

    useUi.setState({ jobId: "job-9", repoId: "repo-9" });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(created).toHaveLength(1));
    expect(created[0].url).toBe("/api/jobs/job-9/events");

    act(() => {
      created[0].emit("progress", {
        phase: "walk",
        state: "running",
        current: 3,
        total: 4,
        progress: 0.75,
        message: "遍历文件",
        file: "src/app.ts",
      });
    });

    expect(useUi.getState().jobProgress?.file).toBe("src/app.ts");
    expect(screen.getByTestId("job-progress-percent").textContent).toBe("75%");
    expect(screen.getByTestId("job-progress-phase").textContent).toBe("遍历文件");
    expect(screen.getByTestId("sidebar-phase").textContent).toContain("遍历文件");

    const reposCalls = () => fetchMock.mock.calls.filter((c) => c[0] === "/api/repos").length;
    const treeCalls = () =>
      fetchMock.mock.calls.filter((c) => String(c[0]).includes("/tree")).length;
    const before = reposCalls();
    const treeBefore = treeCalls();

    act(() => {
      created[0].emit("done", { phase: "done", state: "done" });
    });

    expect(created[0].closed).toBe(true);
    expect(screen.getByTestId("job-progress-phase").textContent).toBe("完成");
    expect(screen.getByTestId("job-progress-percent").textContent).toBe("100%");
    await waitFor(() => expect(reposCalls()).toBeGreaterThan(before));
    // 索引前读到的目录树可能是一次失败/半截结果：终态必须让它重读一次。
    await waitFor(() => expect(treeCalls()).toBeGreaterThan(treeBefore));

    // 切换视图不应重建订阅（依赖只有 jobId）
    fireEvent.click(screen.getByRole("tab", { name: /CST 语法树/ }));
    await waitFor(() => expect(created).toHaveLength(1));
  });

  test("error 事件写入失败态并展示后端 message", async () => {
    const created: FakeEventSource[] = [];
    setJobEventsFactory((url) => {
      const source = new FakeEventSource(url);
      created.push(source);
      return source;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url === "/api/health" ? jsonRes(HEALTH) : jsonRes({ repos: [] }),
      ),
    );

    useUi.setState({ jobId: "job-err" });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    );

    await waitFor(() => expect(created).toHaveLength(1));
    act(() => {
      created[0].emit("progress", {
        phase: "clone",
        state: "running",
        current: 0,
        total: 0,
        progress: 0.3,
        message: "克隆仓库",
      });
    });
    act(() => {
      created[0].emit("error", { state: "error", message: "clone 失败：仓库不存在" });
    });

    expect(useUi.getState().jobProgress?.state).toBe("error");
    expect(created[0].closed).toBe(true);
    expect(screen.getByTestId("job-progress").getAttribute("data-state")).toBe("error");
    expect(screen.getByTestId("job-progress-message").textContent).toContain("clone 失败");
  });

  test("没有 jobId 时不创建 SSE 连接", async () => {
    const created: FakeEventSource[] = [];
    setJobEventsFactory((url) => {
      const source = new FakeEventSource(url);
      created.push(source);
      return source;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) =>
        url === "/api/health" ? jsonRes(HEALTH) : jsonRes({ repos: [] }),
      ),
    );

    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <App />
      </QueryClientProvider>,
    );

    expect(screen.queryByTestId("job-progress")).toBeNull();
    expect(created).toHaveLength(0);
  });
});
