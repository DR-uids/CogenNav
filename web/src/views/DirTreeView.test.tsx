import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

import type { TreeNode } from "../api/client";
import { useUi } from "../stores/ui";
import { DirTreeView } from "./DirTreeView";

const REPO = "repo-1";

const HEALTH = {
  status: "ok",
  version: "0.1.0",
  home: "/tmp/cogen",
  llm_configured: false,
  web_built: true,
};

function file(
  name: string,
  path: string,
  loc: number,
  symbols: number,
  language: string,
): TreeNode {
  return {
    name,
    path,
    type: "file",
    loc,
    files: 1,
    symbols,
    language,
    errorCount: 0,
    children: [],
  };
}

function dir(
  name: string,
  path: string,
  loc: number,
  files: number,
  symbols: number,
  children: TreeNode[] = [],
  errorCount = 0,
): TreeNode {
  return { name, path, type: "dir", loc, files, symbols, language: null, errorCount, children };
}

const FILE_A = file("a.py", "src/a.py", 80, 3, "python");
const FILE_B = file("b.ts", "src/b.ts", 90, 4, "typescript");
/** 被 depth 截断的目录：没有 children 但还有文件 —— 展开时需要再请求一次。 */
const API_DIR = dir("api", "src/api", 30, 1, 1);
const SRC_DIR = dir("src", "src", 200, 3, 7, [FILE_A, FILE_B, API_DIR], 1);
const README = file("README.md", "README.md", 12, 0, "markdown");
const ROOT = dir("repo", "", 212, 4, 7, [SRC_DIR, README], 1);

const API_FILE = file("client.ts", "src/api/client.ts", 30, 1, "typescript");
const API_LOADED = { ...API_DIR, children: [API_FILE] };

function jsonRes(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

type Route = {
  /** 按 path 参数返回子树；返回 undefined 表示 404。 */
  tree?: (path: string) => TreeNode | undefined;
  treeError?: { status: number; detail: string };
  /** GET /api/repos 的返回（决定仓库是否还在索引，见 DirTreeView 的门控）。 */
  repos?: unknown[];
  /** POST /api/repos 的返回（重新索引按钮）。 */
  createResult?: { repoId: string; jobId: string };
};

function installFetch(route: Route = {}) {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    calls.push(url);
    if (url === "/api/health") return jsonRes(HEALTH);

    if (url === "/api/repos") {
      if (init?.method === "POST") {
        return jsonRes(route.createResult ?? { repoId: REPO, jobId: "job-reindex" }, 201);
      }
      return jsonRes({ repos: route.repos ?? [] });
    }

    if (url.startsWith(`/api/repos/${REPO}/tree`)) {
      if (route.treeError) return jsonRes({ detail: route.treeError.detail }, route.treeError.status);
      const path = new URL(url, "http://localhost").searchParams.get("path") ?? "";
      const node = route.tree ? route.tree(path) : path === "" ? ROOT : path === "src/api" ? API_LOADED : undefined;
      if (!node) return jsonRes({ detail: `目录不存在: ${path}` }, 404);
      return jsonRes({ path, node, totalLoc: node.loc, totalFiles: node.files });
    }

    return jsonRes({ repos: route.repos ?? [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <DirTreeView />
    </QueryClientProvider>,
  );
}

function rowOrNull(path: string): HTMLElement | null {
  return document.querySelector(`[data-testid="dir-row"][data-path="${path}"]`);
}

function row(path: string): HTMLElement {
  const el = rowOrNull(path);
  if (!el) throw new Error(`未找到目录行: ${path}`);
  return el;
}

function rect(path: string): HTMLElement {
  const el = document.querySelector(`[data-testid="treemap-rect"][data-path="${path}"]`);
  if (!el) throw new Error(`未找到 Treemap 矩形: ${path}`);
  return el as HTMLElement;
}

const originalOffsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetHeight");
const originalOffsetWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth");

beforeAll(() => {
  // jsdom 没有布局：不补高度的话虚拟列表窗口是 0，什么都渲染不出来（与 CstView 测试同款处理）。
  Object.defineProperty(HTMLElement.prototype, "offsetHeight", {
    configurable: true,
    get: () => 640,
  });
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get: () => 800,
  });
});

afterAll(() => {
  if (originalOffsetHeight) {
    Object.defineProperty(HTMLElement.prototype, "offsetHeight", originalOffsetHeight);
  }
  if (originalOffsetWidth) {
    Object.defineProperty(HTMLElement.prototype, "offsetWidth", originalOffsetWidth);
  }
});

beforeEach(() => {
  useUi.setState({
    activeView: "tree",
    repoId: REPO,
    jobId: null,
    jobProgress: null,
    selectedFile: null,
    selectedNode: null,
  });
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("DirTreeView 目录树", () => {
  test("渲染目录树：根默认展开一层，行上有名/LOC/文件数/符号数", async () => {
    installFetch();
    renderView();

    await waitFor(() => expect(screen.getAllByTestId("dir-row")).toHaveLength(3));
    expect(row("").getAttribute("data-type")).toBe("dir");
    expect(row("").getAttribute("data-depth")).toBe("0");
    expect(row("src").getAttribute("data-depth")).toBe("1");

    const src = row("src");
    expect(src.textContent).toContain("src");
    expect(within(src).getByTestId("dir-row-loc").textContent).toBe("200 lines");
    expect(within(src).getByTestId("dir-row-files").textContent).toBe("3 files");
    expect(within(src).getByTestId("dir-row-symbols").textContent).toBe("7 symbols");
    expect(within(src).getByTestId("dir-row-errors").textContent).toBe("1 error");

    expect(screen.getByTestId("dir-tree-total").textContent).toContain("4 files");
  });

  test("折叠/展开目录：展开 src 后出现子行", async () => {
    installFetch();
    renderView();

    await waitFor(() => expect(screen.getAllByTestId("dir-row")).toHaveLength(3));
    expect(rowOrNull("src/a.py")).toBeNull();

    fireEvent.click(within(row("src")).getByTestId("dir-row-toggle"));
    await waitFor(() => expect(screen.getAllByTestId("dir-row")).toHaveLength(6));
    expect(row("src/a.py").getAttribute("data-type")).toBe("file");
    expect(row("src/api").getAttribute("data-type")).toBe("dir");

    fireEvent.click(within(row("src")).getByTestId("dir-row-toggle"));
    await waitFor(() => expect(screen.getAllByTestId("dir-row")).toHaveLength(3));
  });

  test("被 depth 截断的目录展开时按该目录路径再请求一层", async () => {
    const { calls } = installFetch();
    renderView();

    await waitFor(() => expect(screen.getAllByTestId("dir-row")).toHaveLength(3));
    fireEvent.click(within(row("src")).getByTestId("dir-row-toggle"));
    await waitFor(() => expect(rowOrNull("src/api")).toBeTruthy());

    expect(row("src/api").textContent).toContain("collapsed");
    fireEvent.click(within(row("src/api")).getByTestId("dir-row-toggle"));

    await waitFor(() => expect(rowOrNull("src/api/client.ts")).toBeTruthy());
    expect(
      calls.some((url) => {
        const parsed = new URL(url, "http://localhost");
        return parsed.pathname === `/api/repos/${REPO}/tree` && parsed.searchParams.get("path") === "src/api";
      }),
    ).toBe(true);
    // 已经拿到子树的目录不该再打请求（结果进了缓存）
    fireEvent.click(within(row("src/api")).getByTestId("dir-row-toggle"));
    await waitFor(() => expect(rowOrNull("src/api/client.ts")).toBeNull());
    fireEvent.click(within(row("src/api")).getByTestId("dir-row-toggle"));
    await waitFor(() => expect(rowOrNull("src/api/client.ts")).toBeTruthy());
    const apiCalls = calls.filter((url) => url.includes("path=src%2Fapi"));
    expect(apiCalls).toHaveLength(1);
  });

  test("空态与错误态", async () => {
    installFetch({ treeError: { status: 404, detail: "仓库不存在" } });
    renderView();
    const error = await screen.findByTestId("dir-tree-error");
    expect(error.textContent).toContain("仓库不存在");

    act(() => {
      useUi.setState({ repoId: null });
    });
    const { calls } = installFetch();
    renderView();
    expect(screen.getAllByTestId("dir-tree-no-repo").length).toBeGreaterThan(0);
    expect(calls.filter((url) => url.includes("/tree?"))).toHaveLength(0);
  });
});

describe("DirTreeView Treemap", () => {
  test("按 loc 分面积：每个文件一个矩形，目录是描边矩形", async () => {
    installFetch();
    renderView();

    await waitFor(() => expect(screen.getAllByTestId("treemap-rect")).toHaveLength(3));
    expect(screen.getAllByTestId("treemap-dir").length).toBeGreaterThan(0);

    // 面积正比于 loc：b.ts(90) > a.py(80) > README(12)
    const area = (path: string) => {
      const el = rect(path);
      return Number(el.getAttribute("width")) * Number(el.getAttribute("height"));
    };
    expect(area("src/b.ts")).toBeGreaterThan(area("src/a.py"));
    expect(area("src/a.py")).toBeGreaterThan(area("README.md"));
    expect(rect("src/b.ts").getAttribute("data-language")).toBe("typescript");
    expect(rect("src/b.ts").getAttribute("data-loc")).toBe("90");
  });

  test("hover 显示 tooltip（路径 / LOC / 文件数 / 符号数），移出后消失", async () => {
    installFetch();
    renderView();

    await waitFor(() => expect(screen.getAllByTestId("treemap-rect")).toHaveLength(3));
    expect(screen.queryByTestId("treemap-tooltip")).toBeNull();

    fireEvent.mouseEnter(rect("src/a.py"));
    const tooltip = screen.getByTestId("treemap-tooltip");
    expect(within(tooltip).getByTestId("treemap-tooltip-path").textContent).toBe("src/a.py");
    expect(within(tooltip).getByTestId("treemap-tooltip-loc").textContent).toBe("80");
    expect(within(tooltip).getByTestId("treemap-tooltip-files").textContent).toBe("1");
    expect(within(tooltip).getByTestId("treemap-tooltip-symbols").textContent).toBe("3");
    expect(within(tooltip).getByTestId("treemap-tooltip-language").textContent).toBe("python");

    fireEvent.mouseLeave(rect("src/a.py"));
    expect(screen.queryByTestId("treemap-tooltip")).toBeNull();
  });

  test("点击文件矩形：写 selectedFile 并切到 CST 视图", async () => {
    installFetch();
    renderView();

    await waitFor(() => expect(screen.getAllByTestId("treemap-rect")).toHaveLength(3));
    fireEvent.click(rect("src/a.py"));

    expect(useUi.getState().selectedFile).toBe("src/a.py");
    expect(useUi.getState().activeView).toBe("cst");
  });

  test("点击目录矩形：选中该目录并回写 ?dir=", async () => {
    const replaceSpy = vi.spyOn(window.history, "replaceState");
    installFetch();
    renderView();

    await waitFor(() => expect(screen.getAllByTestId("treemap-dir").length).toBeGreaterThan(0));
    const target = document.querySelector('[data-testid="treemap-dir"][data-path="src"]');
    expect(target).not.toBeNull();
    fireEvent.click(target as HTMLElement);

    expect(screen.getByTestId("treemap-selected-dir").textContent).toContain("src");
    await waitFor(() => {
      const urls = replaceSpy.mock.calls.map((call) => String(call[2]));
      expect(urls.some((url) => url.includes("dir=src"))).toBe(true);
    });
  });

  test("深链 ?dir=src/api 会展开祖先链并选中该目录", async () => {
    installFetch();
    window.history.replaceState(null, "", "/?dir=src%2Fapi");
    renderView();

    await waitFor(() => expect(rowOrNull("src/api")).toBeTruthy());
    expect(row("src/api").getAttribute("data-selected")).toBe("true");
    expect(screen.getByTestId("treemap-selected-dir").textContent).toContain("src/api");
    // 祖先目录 src 被自动展开
    expect(row("src").getAttribute("data-expanded")).toBe("true");
  });

  test("仓库还在索引时不请求目录树：只给等待提示，不缓存失败态", async () => {
    const { calls } = installFetch({
      repos: [{ repoId: REPO, state: "running", target: "owner/repo" }],
    });
    renderView();

    await waitFor(() => expect(screen.getByTestId("dir-tree-indexing")).toBeTruthy());
    expect(screen.getByTestId("dir-tree-indexing").textContent).toContain("indexing finishes");
    // 关键：一次 /tree 都不该发（此前会拿到 409/410 并被无限期缓存）
    expect(calls.some((url) => url.includes("/tree"))).toBe(false);
    expect(screen.queryByTestId("dir-tree-error")).toBeNull();
  });

  test("列表里状态是 done 时照常读目录树", async () => {
    installFetch({ repos: [{ repoId: REPO, state: "done", target: "owner/repo" }] });
    renderView();

    await waitFor(() => expect(screen.getAllByTestId("dir-row")).toHaveLength(3));
    expect(screen.queryByTestId("dir-tree-indexing")).toBeNull();
  });

  test("真 410（快照被清理）时给一键重新索引，并把任务交给 SSE 跟踪", async () => {
    const { fetchMock } = installFetch({
      treeError: { status: 410, detail: "索引快照已不存在，请重新索引" },
      repos: [{ repoId: REPO, state: "done", target: "https://github.com/owner/repo", ref: null }],
    });
    renderView();

    const error = await screen.findByTestId("dir-tree-error");
    expect(error.textContent).toContain("索引快照已不存在");

    fireEvent.click(screen.getByTestId("dir-tree-reindex"));

    await waitFor(() => expect(useUi.getState().jobId).toBe("job-reindex"));
    const post = fetchMock.mock.calls.find((c) => c[1]?.method === "POST");
    expect(post?.[0]).toBe("/api/repos");
    expect(JSON.parse(String(post?.[1]?.body))).toMatchObject({
      target: "https://github.com/owner/repo",
    });
  });
});

describe("DirTreeView 语言分布条", () => {
  test("默认按 LOC 统计，可切到按文件数", async () => {
    installFetch();
    renderView();

    await waitFor(() => expect(screen.getAllByTestId("lang-legend-item")).toHaveLength(3));
    expect(screen.getByTestId("lang-bar-total").textContent).toBe("182 lines");
    expect(screen.getByTestId("lang-mode-loc").getAttribute("aria-pressed")).toBe("true");

    const first = screen.getAllByTestId("lang-legend-item")[0];
    expect(first.getAttribute("data-language")).toBe("typescript");
    expect(first.getAttribute("data-value")).toBe("90");

    fireEvent.click(screen.getByTestId("lang-mode-files"));
    expect(screen.getByTestId("lang-bar-total").textContent).toBe("3 files");
    expect(screen.getByTestId("lang-mode-files").getAttribute("aria-pressed")).toBe("true");
  });
});
