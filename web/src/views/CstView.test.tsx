import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test, vi } from "vitest";

import type { CstNode, FileText, RepoFile } from "../api/client";
import { highlightToHtml } from "../lib/shiki";
import { useUi } from "../stores/ui";
import { CstView } from "./CstView";

// shiki 会在 jsdom 里尝试实例化 wasm，这里整体替换成假的 HTML 生成器；
// lib/shiki.test.ts 单独覆盖真实高亮器的行为与失败路径。
vi.mock("../lib/shiki", () => ({ highlightToHtml: vi.fn() }));

const highlightMock = vi.mocked(highlightToHtml);
const REPO = "repo-1";

const HEALTH = {
  status: "ok",
  version: "0.1.0",
  home: "/tmp/cogen",
  llm_configured: false,
  web_built: true,
};

const FILES: RepoFile[] = [
  {
    path: "src/a.py",
    language: "python",
    size: 120,
    loc: 8,
    nodeCount: 12,
    parseOk: true,
    error: null,
  },
  {
    path: "src/b.js",
    language: "javascript",
    size: 90,
    loc: 5,
    nodeCount: 9,
    parseOk: true,
    error: null,
  },
  {
    path: "Makefile",
    language: "make",
    size: 20,
    loc: 3,
    nodeCount: 0,
    parseOk: false,
    error: "解析失败：unexpected token",
  },
];

const FILE_TEXT: FileText = {
  path: "src/a.py",
  language: "python",
  size: 24,
  lines: 3,
  text: "def f():\n    return 1\n",
  truncated: false,
};

function leaf(
  type: string,
  start: [number, number],
  end: [number, number],
  text: string | null = null,
  extra: Partial<CstNode> = {},
): CstNode {
  return {
    type,
    named: true,
    field: null,
    start,
    end,
    startByte: start[1],
    endByte: end[1],
    childCount: 0,
    error: false,
    missing: false,
    truncated: false,
    text,
    children: [],
    ...extra,
  };
}

const IDENTIFIER = leaf("identifier", [0, 4], [0, 5], "f", { field: "name" });

/** 树里第一次返回的 block：受 depth 限制没有子节点。 */
const BLOCK: CstNode = {
  type: "block",
  named: true,
  field: "body",
  start: [0, 9],
  end: [1, 10],
  startByte: 9,
  endByte: 33,
  childCount: 2,
  error: false,
  missing: false,
  truncated: true,
  text: null,
  children: [],
};

const FUNCTION: CstNode = {
  type: "function_definition",
  named: true,
  field: null,
  start: [0, 0],
  end: [1, 10],
  startByte: 0,
  endByte: 33,
  childCount: 2,
  error: false,
  missing: false,
  truncated: false,
  text: null,
  children: [IDENTIFIER, BLOCK],
};

const ROOT: CstNode = {
  type: "module",
  named: true,
  field: null,
  start: [0, 0],
  end: [2, 0],
  startByte: 0,
  endByte: 34,
  childCount: 1,
  error: false,
  missing: false,
  truncated: false,
  text: null,
  children: [FUNCTION],
};

/** 展开 block（nodePath=0.1）时后端返回的子树。 */
const BLOCK_CHILDREN: CstNode[] = [
  leaf("return_statement", [1, 4], [1, 12], "return 1"),
  leaf("integer", [1, 11], [1, 12], "1"),
];
const BLOCK_FULL: CstNode = { ...BLOCK, truncated: false, children: BLOCK_CHILDREN };

const CST_BY_NODE_PATH: Record<string, CstNode> = {
  "": ROOT,
  "0": FUNCTION,
  "0.0": IDENTIFIER,
  "0.1": BLOCK_FULL,
  "0.1.0": BLOCK_CHILDREN[0],
  "0.1.1": BLOCK_CHILDREN[1],
};

function jsonRes(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

type Route = {
  files?: RepoFile[];
  total?: number;
  filesForQuery?: (q: string) => RepoFile[];
  file?: FileText;
  fileError?: { status: number; detail: string };
  cst?: (nodePath: string) => CstNode | undefined;
  cstError?: { status: number; detail: string };
};

function installFetch(route: Route = {}) {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url === "/api/health") return jsonRes(HEALTH);

    if (url.startsWith(`/api/repos/${REPO}/files`)) {
      const q = new URL(url, "http://localhost").searchParams.get("q") ?? "";
      const files = route.filesForQuery ? route.filesForQuery(q) : (route.files ?? FILES);
      return jsonRes({ total: route.total ?? files.length, files });
    }

    if (url.startsWith(`/api/repos/${REPO}/file`)) {
      if (route.fileError) return jsonRes({ detail: route.fileError.detail }, route.fileError.status);
      return jsonRes(route.file ?? FILE_TEXT);
    }

    if (url.startsWith(`/api/repos/${REPO}/cst`)) {
      if (route.cstError) return jsonRes({ detail: route.cstError.detail }, route.cstError.status);
      const nodePath = new URL(url, "http://localhost").searchParams.get("nodePath") ?? "";
      const node = route.cst ? route.cst(nodePath) : CST_BY_NODE_PATH[nodePath];
      if (!node) return jsonRes({ detail: `节点不存在: ${nodePath}` }, 404);
      return jsonRes({
        path: FILE_TEXT.path,
        language: "python",
        nodePath,
        depth: 4,
        totalNodes: 42,
        node,
      });
    }

    return jsonRes({ repos: [] });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CstView />
    </QueryClientProvider>,
  );
}

function fileItem(path: string): HTMLElement {
  const el = document.querySelector(`[data-testid="cst-file-item"][data-path="${path}"]`);
  if (!el) throw new Error(`未找到文件项: ${path}`);
  return el as HTMLElement;
}

function nodeRowOrNull(path: string): HTMLElement | null {
  return document.querySelector(`[data-testid="cst-node-row"][data-nodepath="${path}"]`);
}

function nodeRow(path: string): HTMLElement {
  const el = nodeRowOrNull(path);
  if (!el) throw new Error(`未找到树节点行: ${path}`);
  return el;
}

async function waitForRow(path: string): Promise<HTMLElement> {
  await waitFor(() => expect(nodeRowOrNull(path)).toBeTruthy());
  return nodeRow(path);
}

/** 点某个节点左侧的展开箭头（会按需触发懒加载请求）。 */
function toggleNode(path: string) {
  fireEvent.click(within(nodeRow(path)).getByTestId("cst-node-toggle"));
}

function cstCalls(calls: string[]): string[] {
  return calls.filter((url) => url.includes("/cst?"));
}

const originalOffsetHeight = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetHeight");
const originalOffsetWidth = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "offsetWidth");

beforeAll(() => {
  // jsdom 没有布局：offsetHeight 恒为 0 会让虚拟列表算出空窗口，什么都渲染不出来。
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
    activeView: "cst",
    repoId: REPO,
    jobId: null,
    jobProgress: null,
    selectedFile: null,
    selectedNode: null,
  });
  window.history.replaceState(null, "", "/");
  highlightMock.mockReset();
  highlightMock.mockImplementation(async (code, _language, ranges) => {
    const body =
      ranges && ranges.length > 0 ? `<mark class="cst-source-mark">${code}</mark>` : code;
    return `<pre class="shiki github-dark"><code><span class="line">${body}</span></code></pre>`;
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("CstView 文件列表", () => {
  test("渲染文件列表：路径 / 语言 / 行数 / 解析失败标记", async () => {
    installFetch();
    renderView();

    await waitFor(() => expect(screen.getAllByTestId("cst-file-item")).toHaveLength(3));
    expect(screen.getByTestId("cst-file-total").textContent).toBe("共 3 个文件");
    expect(fileItem("src/a.py").textContent).toContain("src/a.py");
    expect(fileItem("src/a.py").textContent).toContain("python");
    expect(fileItem("src/a.py").textContent).toContain("8 行");
    expect(
      within(fileItem("Makefile")).getByTestId("cst-file-parse-error").textContent,
    ).toContain("解析失败");
  });

  test("搜索防抖：连续输入只发一次带 q 的请求，并按结果过滤", async () => {
    const { calls } = installFetch({
      filesForQuery: (q) => FILES.filter((file) => file.path.includes(q)),
    });
    renderView();

    await waitFor(() => expect(screen.getAllByTestId("cst-file-item")).toHaveLength(3));

    const search = screen.getByTestId("cst-file-search");
    fireEvent.change(search, { target: { value: "M" } });
    fireEvent.change(search, { target: { value: "Ma" } });
    fireEvent.change(search, { target: { value: "Mak" } });
    fireEvent.change(search, { target: { value: "Make" } });

    await waitFor(() => expect(screen.getAllByTestId("cst-file-item")).toHaveLength(1));
    expect(fileItem("Makefile")).toBeTruthy();

    const withQuery = calls.filter((url) => url.includes("/files?") && url.includes("q=Make"));
    expect(withQuery).toHaveLength(1);
    // 中间态（q=M/Ma/Mak）没有被请求过
    expect(calls.some((url) => url.includes("q=M&") || url.includes("q=Ma&"))).toBe(false);
  });

  test("未选择仓库时给出空态且不发文件请求", async () => {
    const { calls } = installFetch();
    useUi.setState({ repoId: null });
    renderView();

    expect(screen.getByTestId("cst-no-repo")).toBeTruthy();
    expect(screen.getByTestId("cst-tree-empty")).toBeTruthy();
    expect(screen.getByTestId("cst-source-empty")).toBeTruthy();
    expect(calls.filter((url) => url.includes("/files?"))).toHaveLength(0);
  });
});

describe("CstView 语法树", () => {
  test("选中文件后用 depth=4 请求根节点并渲染树", async () => {
    const { calls } = installFetch();
    useUi.setState({ selectedFile: "src/a.py" });
    renderView();

    await waitForRow("");

    expect(nodeRow("").textContent).toContain("module");
    expect(nodeRow("").textContent).toContain("1:1–3:1");
    expect(screen.getByTestId("cst-total-nodes").textContent).toBe("共 42 个节点");
    expect(cstCalls(calls)).toEqual([`/api/repos/${REPO}/cst?path=src%2Fa.py&nodePath=&depth=4`]);
  });

  test("展开 truncated 节点会按该节点 nodePath 请求并合并子节点", async () => {
    const { calls } = installFetch();
    useUi.setState({ selectedFile: "src/a.py" });
    renderView();

    await waitForRow("");

    // 展开 function_definition（其子节点已内联，不该产生请求）
    toggleNode("0");
    await waitForRow("0.1");
    expect(nodeRow("0.1").textContent).toContain("block");
    expect(cstCalls(calls)).toHaveLength(1);

    // 展开 block：truncated=true，触发 nodePath=0.1 的下钻
    toggleNode("0.1");
    await waitForRow("0.1.0");
    expect(nodeRow("0.1.0").textContent).toContain("return_statement");
    expect(nodeRow("0.1.0").textContent).toContain("return 1");
    expect(nodeRow("0.1.1").textContent).toContain("integer");

    await waitFor(() =>
      expect(cstCalls(calls)).toContain(
        `/api/repos/${REPO}/cst?path=src%2Fa.py&nodePath=0.1&depth=4`,
      ),
    );

    // 折叠后再展开不应重复请求（结果已在缓存里）
    toggleNode("0.1");
    await waitFor(() => expect(nodeRowOrNull("0.1.0")).toBeNull());
    toggleNode("0.1");
    await waitForRow("0.1.0");
    expect(cstCalls(calls)).toHaveLength(2);
  });

  test("选中节点：写入 store 并在源码面板高亮其范围", async () => {
    installFetch();
    useUi.setState({ selectedFile: "src/a.py" });
    const { container } = renderView();

    await waitForRow("");
    toggleNode("0");
    await waitForRow("0.0");

    fireEvent.click(nodeRow("0.0"));

    expect(useUi.getState().selectedNode).toBe("0.0");
    await waitFor(() => expect(nodeRow("0.0").getAttribute("data-selected")).toBe("true"));
    await waitFor(() =>
      expect(container.querySelector("mark.cst-source-mark")).not.toBeNull(),
    );
    // 传给高亮器的是该节点的行列范围
    expect(highlightMock).toHaveBeenCalledWith(FILE_TEXT.text, "python", [
      { start: [0, 4], end: [0, 5] },
    ]);
    expect(screen.getByTestId("cst-source-node").textContent).toContain("identifier");
  });

  test("shiki 失败时回退纯文本，仍保留范围标记", async () => {
    highlightMock.mockResolvedValue(null);
    installFetch();
    useUi.setState({ selectedFile: "src/a.py" });
    const { container } = renderView();

    await waitFor(() =>
      expect(screen.getByTestId("cst-source-plain").textContent).toContain("def f():"),
    );

    await waitForRow("");
    toggleNode("0");
    await waitForRow("0.0");
    fireEvent.click(nodeRow("0.0"));

    await waitFor(() => expect(container.querySelector("mark.cst-source-mark")).not.toBeNull());
    expect(screen.getByTestId("cst-source-plain").textContent).toContain("return 1");
    // 命中行整行加背景
    expect(screen.getByTestId("cst-source-active-line").getAttribute("data-line")).toBe("1");
  });

  test("415：提示该语言没有可用语法，源码仍可浏览", async () => {
    installFetch({
      cstError: { status: 415, detail: "该语言没有可用的语法" },
      files: [{ ...FILES[0], path: "bin/tool", language: "binary" }],
    });
    useUi.setState({ selectedFile: "bin/tool" });
    renderView();

    const notice = await screen.findByTestId("cst-no-grammar");
    expect(notice.textContent).toContain("该语言没有可用的语法");
    expect(notice.textContent).toContain("右侧源码仍可正常浏览");
    expect(screen.getByTestId("cst-source-highlighted").textContent).toContain("def f():");
  });

  test("子树请求失败时行内提示并可重试", async () => {
    // 只让 block 这一棵子树失败（其余节点照常返回）
    const { calls } = installFetch({
      cst: (nodePath) => (nodePath === "0.1" ? undefined : CST_BY_NODE_PATH[nodePath]),
    });
    useUi.setState({ selectedFile: "src/a.py" });
    renderView();

    await waitForRow("0");
    toggleNode("0");
    await waitForRow("0.1");
    toggleNode("0.1");

    const failure = await screen.findByTestId("cst-subtree-error");
    expect(failure.textContent).toContain("节点不存在: 0.1");
    expect(nodeRowOrNull("0.1.0")).toBeNull();
    // 失败后不再显示「加载中…」
    expect(nodeRow("0.1").textContent).not.toContain("加载中");

    const before = cstCalls(calls).filter((url) => url.includes("nodePath=0.1")).length;
    fireEvent.click(screen.getByTestId("cst-subtree-retry"));
    await waitFor(() =>
      expect(cstCalls(calls).filter((url) => url.includes("nodePath=0.1")).length).toBeGreaterThan(
        before,
      ),
    );
  });

  test("源码 404 时展示后端 detail", async () => {
    installFetch({ fileError: { status: 404, detail: "文件不存在或超出仓库范围" } });
    useUi.setState({ selectedFile: "src/missing.py" });
    renderView();

    const error = await screen.findByTestId("cst-source-error");
    expect(error.textContent).toContain("文件不存在或超出仓库范围");
  });
});

describe("CstView 选中态与 URL", () => {
  test("选择文件/节点的变化同步到 URL（replaceState）", async () => {
    const replaceSpy = vi.spyOn(window.history, "replaceState");
    installFetch();
    renderView();

    await waitFor(() => expect(screen.getAllByTestId("cst-file-item")).toHaveLength(3));
    fireEvent.click(fileItem("src/a.py"));

    await waitFor(() => {
      const urls = replaceSpy.mock.calls.map((call) => String(call[2]));
      expect(urls.some((url) => url.includes("file=src%2Fa.py"))).toBe(true);
    });

    await waitForRow("");
    toggleNode("0");
    await waitForRow("0.0");
    fireEvent.click(nodeRow("0.0"));

    await waitFor(() => {
      const urls = replaceSpy.mock.calls.map((call) => String(call[2]));
      expect(urls.some((url) => url.includes("node=0.0"))).toBe(true);
    });
  });

  test("深链 ?file=&node= 会恢复选中并自动展开祖先链", async () => {
    installFetch();
    window.history.replaceState(null, "", "/?file=src/a.py&node=0.1");
    renderView();

    const blockRow = await waitForRow("0.1");
    await waitFor(() => expect(blockRow.getAttribute("data-selected")).toBe("true"));
    // 祖先 module / function_definition 被自动展开
    expect(nodeRow("").getAttribute("data-selected")).toBe("false");
    expect(nodeRow("0")).toBeTruthy();
    expect(useUi.getState().selectedFile).toBe("src/a.py");
    expect(useUi.getState().selectedNode).toBe("0.1");
    await waitFor(() => expect(screen.getByTestId("cst-source-node").textContent).toContain("block"));
  });

  test("切换文件会清空已选节点", async () => {
    installFetch();
    useUi.setState({ selectedFile: "src/a.py", selectedNode: "0.0" });
    renderView();

    await waitFor(() => expect(screen.getAllByTestId("cst-file-item")).toHaveLength(3));
    fireEvent.click(fileItem("src/b.js"));

    expect(useUi.getState().selectedFile).toBe("src/b.js");
    expect(useUi.getState().selectedNode).toBeNull();
  });
});
