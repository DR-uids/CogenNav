import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import App from "../App";
import type { AnalysisResponse, GraphEdge, GraphNode } from "../api/client";
import {
  resetGraphRenderers,
  setGraphRenderers,
  type DagRendererProps,
  type ForceRendererProps,
} from "../components/graph/renderers";
import { useUi } from "../stores/ui";
import { GraphView } from "./GraphView";

// 双重保险：真实渲染器（sigma / react-flow）在 jsdom 里不可用。
// 即便某个用例忘了注入替身，这两个模块也只是空组件，不会被真正加载。
vi.mock("../components/graph/ForceGraph", () => ({ default: () => null }));
vi.mock("../components/graph/LayeredDag", () => ({ default: () => null }));

const REPO = "repo-1";

const HEALTH = {
  status: "ok",
  version: "0.1.0",
  home: "/tmp/cogen",
  llm_configured: false,
  web_built: true,
};

function node(
  id: string,
  name: string,
  kind: string,
  community: number,
  degree: number,
): GraphNode {
  return {
    id,
    kind,
    name,
    qualified: `auth.${name}`,
    file: `src/${name}.ts`,
    language: "typescript",
    community,
    degree,
    inDegree: 1,
    outDegree: 1,
    start: [3, 0],
  };
}

const NODES: GraphNode[] = [
  node("s1", "login", "function", 1, 5),
  node("s2", "Session", "class", 1, 4),
  node("s3", "verify", "method", 2, 2),
];

const EDGES: GraphEdge[] = [
  { source: "s1", target: "s2", relation: "calls", confidence: "extracted", file: "src/login.ts", start: [5, 2] },
  { source: "s2", target: "s3", relation: "calls", confidence: "ambiguous", file: "src/Session.ts", start: [9, 4] },
];

const ANALYSIS: AnalysisResponse = {
  communities: [
    {
      id: 1,
      name: "认证",
      size: 2,
      cohesion: 0.5,
      namedBy: "llm",
      topSymbols: [{ id: "s1", name: "login", kind: "function", file: "src/login.ts" }],
    },
    { id: 2, name: "会话", size: 1, cohesion: null, namedBy: "heuristic", topSymbols: [] },
  ],
  godNodes: [
    { id: "s1", name: "login", kind: "function", file: "src/login.ts", degree: 5, inDegree: 2, outDegree: 3 },
  ],
  cycles: [],
  orphans: [],
  stats: {
    nodes: 3,
    edges: 2,
    byKind: { function: 1, class: 1, method: 1 },
    byRelation: { calls: 2 },
    byConfidence: { extracted: 1, ambiguous: 1 },
    resolvedCallRate: 0.8,
  },
};

const SURFACE = {
  status: "ok",
  version: "0.1.0",
  home: "/tmp/cogen",
  llm_configured: false,
  web_built: false,
};

const TREE = {
  path: "",
  node: {
    name: "repo",
    path: "",
    type: "dir",
    loc: 10,
    files: 1,
    symbols: 3,
    language: null,
    errorCount: 0,
    children: [
      { name: "login.ts", path: "src/login.ts", type: "file", loc: 10, files: 1, symbols: 3, language: "typescript", errorCount: 0, children: [] },
    ],
  },
  totalLoc: 10,
  totalFiles: 1,
};

function jsonRes(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function symbolPayload(nodeId: string) {
  const target = NODES.find((item) => item.id === nodeId) ?? NODES[0];
  return {
    node: target,
    incoming: [
      {
        source: "s9",
        sourceName: "handler",
        relation: "calls",
        confidence: "extracted",
        file: "src/http.ts",
        start: [12, 1],
      },
    ],
    outgoing: [
      {
        target: "s2",
        targetName: "Session",
        relation: "calls",
        confidence: "extracted",
        file: "src/login.ts",
        start: [5, 2],
      },
    ],
    community: { id: 1, name: "认证" },
    definition: {
      file: "src/login.ts",
      start: [3, 0],
      end: [6, 1],
      snippet: "export function login() {}",
    },
  };
}

type Route = {
  graph?: { nodes: GraphNode[]; edges: GraphEdge[]; total?: { nodes: number; edges: number }; truncated?: boolean };
  impact?: unknown;
  search?: unknown;
};

function installFetch(route: Route = {}) {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url === "/api/health") return jsonRes(HEALTH);

    const parsed = new URL(url, "http://localhost");
    const path = parsed.pathname;

    if (path.endsWith("/analysis")) return jsonRes(ANALYSIS);
    if (path.endsWith("/graph/neighbors")) {
      return jsonRes({ nodes: NODES, edges: EDGES, total: { nodes: 3, edges: 2 }, truncated: false });
    }
    if (path.endsWith("/graph/impact")) {
      return jsonRes(
        route.impact ?? {
          root: NODES[0],
          nodes: NODES,
          edges: EDGES,
          files: [
            { path: "src/Session.ts", count: 3 },
            { path: "src/login.ts", count: 1 },
          ],
          truncated: false,
        },
      );
    }
    if (path.endsWith("/graph/path")) return jsonRes({ found: false, nodes: [], edges: [] });
    if (path.endsWith("/graph")) {
      const override = route.graph;
      return jsonRes({
        nodes: override?.nodes ?? NODES,
        edges: override?.edges ?? EDGES,
        total: override?.total ?? { nodes: 3, edges: 2 },
        truncated: override?.truncated ?? false,
      });
    }
    if (path.endsWith("/search")) {
      return jsonRes(
        route.search ?? {
          results: [
            {
              id: "s3",
              kind: "method",
              name: "verify",
              qualified: "auth.Session.verify",
              file: "src/Session.ts",
              start: [9, 4],
              snippet: "verify(token)",
            },
          ],
          total: 1,
        },
      );
    }
    if (path.endsWith("/symbol")) {
      return jsonRes(symbolPayload(parsed.searchParams.get("nodeId") ?? "s1"));
    }
    if (path.endsWith("/tree")) return jsonRes(TREE);
    if (path.endsWith("/files")) return jsonRes({ total: 0, files: [] });
    return jsonRes(SURFACE);
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}

/**
 * 替身渲染器：sigma / react-flow 在 jsdom 里跑不起来，这里断言「数据进了渲染器」。
 * 真实实现是 lazy import（见 components/graph/renderers.tsx），测试里根本不会加载。
 */
function ForceSpy({ nodes, edges, selectedId, onSelectNode, hops }: ForceRendererProps) {
  return (
    <div
      data-testid="force-renderer"
      data-nodes={nodes.length}
      data-edges={edges.length}
      data-selected={selectedId ?? ""}
    >
      {nodes.map((item) => (
        <button
          key={item.id}
          type="button"
          data-testid="renderer-node"
          data-id={item.id}
          data-hop={hops?.get(item.id) ?? ""}
          onClick={() => onSelectNode(item.id)}
        >
          {item.name}
        </button>
      ))}
    </div>
  );
}

function DagSpy({ nodes, edges, selectedId, direction }: DagRendererProps) {
  return (
    <div
      data-testid="dag-renderer"
      data-nodes={nodes.length}
      data-edges={edges.length}
      data-direction={direction}
      data-selected={selectedId ?? ""}
    />
  );
}

function renderView() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <GraphView />
    </QueryClientProvider>,
  );
}

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

/** 取某类接口的请求参数列表（按 path 后缀过滤）。 */
function paramsOf(calls: readonly string[], suffix: string): URLSearchParams[] {
  return calls
    .filter((url) => new URL(url, "http://localhost").pathname === suffix)
    .map((url) => new URL(url, "http://localhost").searchParams);
}

const GRAPH = `/api/repos/${REPO}/graph`;
const NEIGHBORS = `/api/repos/${REPO}/graph/neighbors`;
const IMPACT = `/api/repos/${REPO}/graph/impact`;

beforeEach(() => {
  // 先清空再注入：清除动作发生在挂载之前，不会触发额外渲染。
  resetGraphRenderers();
  setGraphRenderers({ force: ForceSpy, dag: DagSpy });
  useUi.setState({
    activeView: "graph",
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

describe("GraphView 力导向与过滤", () => {
  test("默认请求全图：不带 kinds/relations，默认隐藏 AMBIGUOUS", async () => {
    const { calls } = installFetch();
    renderView();

    await waitFor(() => expect(screen.getByTestId("force-renderer")).toBeTruthy());
    expect(screen.getByTestId("force-renderer").getAttribute("data-nodes")).toBe("3");
    expect(screen.getByTestId("force-renderer").getAttribute("data-edges")).toBe("2");

    const [params] = paramsOf(calls, GRAPH);
    expect(params?.get("limit")).toBe("1500");
    expect(params?.get("kinds")).toBeNull();
    expect(params?.get("relations")).toBeNull();
    expect(params?.has("focus")).toBe(false);
    expect(params?.get("confidence")?.split(",")).toEqual(["extracted", "inferred"]);
    expect((screen.getByTestId("filter-hide-ambiguous") as HTMLInputElement).checked).toBe(true);
    expect(
      (document.querySelector('[data-testid="filter-confidence"][data-value="ambiguous"]') as HTMLInputElement)
        .disabled,
    ).toBe(true);
  });

  test("过滤面板改变请求参数：取消一个 kind、放开 AMBIGUOUS", async () => {
    const { calls } = installFetch();
    renderView();

    // 等分析结果落地（类型清单由 /analysis 的 byKind 驱动），否则勾选项来自兜底清单。
    await waitFor(() =>
      expect(document.querySelectorAll('[data-testid="filter-kind"]').length).toBe(3),
    );
    const kindCheckbox = document.querySelector(
      '[data-testid="filter-kind"][data-value="class"]',
    ) as HTMLInputElement;
    fireEvent.click(kindCheckbox);

    await waitFor(() => {
      const last = paramsOf(calls, GRAPH).at(-1);
      expect(last?.get("kinds")).toBe("function,method");
    });

    const before = paramsOf(calls, GRAPH).length;
    fireEvent.click(screen.getByTestId("filter-hide-ambiguous"));

    await waitFor(() => {
      const all = paramsOf(calls, GRAPH);
      expect(all.length).toBeGreaterThan(before);
      const last = all.at(-1);
      // 放开后 AMBIGUOUS 重新参与（要么显式列出，要么不传参数 = 全都要）
      expect(last?.get("confidence") ?? "extracted,inferred,ambiguous").toContain("ambiguous");
      // 之前的 kind 过滤仍然生效
      expect(last?.get("kinds")).toBe("function,method");
    });

    // AMBIGUOUS 复选框重新可用；显式取消 inferred 后剩下 extracted + ambiguous
    const ambiguous = document.querySelector(
      '[data-testid="filter-confidence"][data-value="ambiguous"]',
    ) as HTMLInputElement;
    expect(ambiguous.disabled).toBe(false);
    fireEvent.click(
      document.querySelector('[data-testid="filter-confidence"][data-value="inferred"]') as HTMLInputElement,
    );
    await waitFor(() => {
      const last = paramsOf(calls, GRAPH).at(-1);
      expect(last?.get("confidence")?.split(",")).toEqual(["extracted", "ambiguous"]);
      expect(last?.get("kinds")).toBe("function,method");
    });
  });

  test("truncated 时提示截断，点「显示更多」把 limit 翻倍", async () => {
    const { calls } = installFetch({
      graph: { nodes: NODES, edges: EDGES, total: { nodes: 9000, edges: 20 }, truncated: true },
    });
    renderView();

    const banner = await screen.findByTestId("graph-truncated");
    expect(banner.textContent).toContain("结果已截断");
    expect(banner.textContent).toContain("3");
    expect(banner.textContent).toContain("9000");
    expect(screen.getByTestId("graph-large-warning").textContent).toContain("5000");

    fireEvent.click(screen.getByTestId("graph-show-more"));
    await waitFor(() => {
      const last = paramsOf(calls, GRAPH).at(-1);
      expect(last?.get("limit")).toBe("3000");
    });
  });

  test("未选择仓库时给出空态且不发图谱请求", () => {
    const { calls } = installFetch();
    useUi.setState({ repoId: null });
    renderView();

    expect(screen.getByTestId("graph-no-repo")).toBeTruthy();
    expect(paramsOf(calls, GRAPH)).toHaveLength(0);
    expect(paramsOf(calls, `/api/repos/${REPO}/analysis`)).toHaveLength(0);
  });
});

describe("GraphView 搜索与 Inspector", () => {
  test("搜索选中结果 → 设为焦点并拉取符号卡片", async () => {
    const { calls } = installFetch();
    const replaceSpy = vi.spyOn(window.history, "replaceState");
    renderView();

    await waitFor(() => expect(screen.getByTestId("force-renderer")).toBeTruthy());

    fireEvent.change(screen.getByTestId("graph-search-input"), { target: { value: "verify" } });

    const result = await screen.findByTestId("graph-search-result");
    expect(result.getAttribute("data-id")).toBe("s3");
    expect(screen.getByTestId("graph-search-total").textContent).toContain("命中 1 个");

    fireEvent.click(result);

    // 焦点写进 URL，并以该节点取邻域
    await waitFor(() => {
      const urls = replaceSpy.mock.calls.map((call) => String(call[2]));
      expect(urls.some((url) => url.includes("focus=s3"))).toBe(true);
    });
    await waitFor(() => {
      const last = paramsOf(calls, GRAPH).at(-1);
      expect(last?.get("focus")).toBe("s3");
      expect(last?.get("depth")).toBe("2");
    });

    // 符号卡片：请求 /symbol 并渲染
    await waitFor(() => expect(paramsOf(calls, `/api/repos/${REPO}/symbol`).length).toBe(1));
    expect(screen.getByTestId("symbol-card")).toBeTruthy();
  });

  test("点击节点 → SymbolCard 渲染出入边，点边跳到另一个符号", async () => {
    const { calls } = installFetch();
    renderView();

    await waitFor(() => expect(screen.getByTestId("force-renderer")).toBeTruthy());
    const login = document.querySelector('[data-testid="renderer-node"][data-id="s1"]');
    expect(login).not.toBeNull();
    fireEvent.click(login as HTMLElement);

    const card = await screen.findByTestId("symbol-card");
    expect(within(card).getByTestId("symbol-name").textContent).toBe("login");
    expect(within(card).getByTestId("symbol-incoming-count").textContent).toBe("1");
    expect(within(card).getByTestId("symbol-outgoing-count").textContent).toBe("1");

    const incoming = within(card).getByTestId("symbol-incoming-item");
    expect(incoming.getAttribute("data-node")).toBe("s9");
    expect(incoming.getAttribute("data-relation")).toBe("calls");

    const outgoing = within(card).getByTestId("symbol-outgoing-item");
    expect(outgoing.getAttribute("data-node")).toBe("s2");

    // 点出边 → 切到 Session 的符号卡片
    fireEvent.click(outgoing);
    await waitFor(() =>
      expect(paramsOf(calls, `/api/repos/${REPO}/symbol`).at(-1)?.get("nodeId")).toBe("s2"),
    );
    await waitFor(() => expect(screen.getByTestId("symbol-name").textContent).toBe("Session"));
  });

  test("「查看语法树」联动 CST：写文件 + 清节点 + 切视图", async () => {
    installFetch();
    renderView();

    await waitFor(() => expect(screen.getByTestId("force-renderer")).toBeTruthy());
    fireEvent.click(document.querySelector('[data-testid="renderer-node"][data-id="s1"]') as HTMLElement);

    await screen.findByTestId("symbol-card");
    fireEvent.click(screen.getByTestId("symbol-open-cst"));

    expect(useUi.getState().selectedFile).toBe("src/login.ts");
    expect(useUi.getState().selectedNode).toBe("");
    expect(useUi.getState().activeView).toBe("cst");
  });
});

describe("GraphView 三种模式", () => {
  test("切到分层 DAG：请求 /graph/neighbors，带方向与默认焦点", async () => {
    const { calls } = installFetch();
    renderView();

    await waitFor(() => expect(screen.getByTestId("force-renderer")).toBeTruthy());
    fireEvent.click(screen.getByTestId("graph-mode-dag"));

    await waitFor(() => expect(screen.getByTestId("dag-renderer")).toBeTruthy());
    expect(screen.getByTestId("dag-renderer").getAttribute("data-direction")).toBe("both");
    expect(screen.getByTestId("dag-renderer").getAttribute("data-nodes")).toBe("3");

    const [params] = paramsOf(calls, NEIGHBORS);
    expect(params?.get("nodeId")).toBe("s1");
    expect(params?.get("depth")).toBe("3");
    expect(params?.get("direction")).toBe("both");
    expect(params?.get("relations")).toBe("calls,imports,extends");
    expect(params?.get("limit")).toBe("400");
    expect(screen.getByTestId("graph-focus-select")).toBeTruthy();

    fireEvent.click(screen.getByTestId("dag-direction-out"));
    await waitFor(() => {
      const last = paramsOf(calls, NEIGHBORS).at(-1);
      expect(last?.get("direction")).toBe("out");
    });
    expect(screen.getByTestId("dag-renderer").getAttribute("data-direction")).toBe("out");
  });

  test("切到影响面：请求 /graph/impact，节点按跳数着色并列出需回归文件", async () => {
    const { calls } = installFetch();
    renderView();

    await waitFor(() => expect(screen.getByTestId("force-renderer")).toBeTruthy());
    fireEvent.click(screen.getByTestId("graph-mode-impact"));

    await waitFor(() => expect(screen.getByTestId("impact-panel")).toBeTruthy());
    const [params] = paramsOf(calls, IMPACT);
    expect(params?.get("nodeId")).toBe("s1");
    expect(params?.get("depth")).toBe("3");
    expect(params?.get("relations")).toBe("calls,references,imports");

    // 跳数：根 0 跳、下游 1/2 跳
    await waitFor(() => expect(screen.getByTestId("force-renderer")).toBeTruthy());
    const hopOf = (id: string) =>
      document.querySelector(`[data-testid="renderer-node"][data-id="${id}"]`)?.getAttribute("data-hop");
    await waitFor(() => expect(hopOf("s1")).toBe("0"));
    expect(hopOf("s2")).toBe("1");
    expect(hopOf("s3")).toBe("2");
    expect(screen.getAllByTestId("legend-hop")).toHaveLength(6);

    // 需回归的文件：按 count 降序
    const files = screen.getAllByTestId("impact-file");
    expect(files).toHaveLength(2);
    expect(files[0].getAttribute("data-path")).toBe("src/Session.ts");
    expect(files[0].getAttribute("data-count")).toBe("3");
    expect(screen.getByTestId("impact-file-count").textContent).toBe("2 个");

    // 点文件 → 去 CST
    fireEvent.click(files[0]);
    expect(useUi.getState().selectedFile).toBe("src/Session.ts");
    expect(useUi.getState().activeView).toBe("cst");
  });
});

describe("GraphView 深链", () => {
  test("?view=graph&mode=impact&focus=s2 → 进入图谱并围绕该焦点请求", async () => {
    const { calls } = installFetch();
    window.history.replaceState(null, "", "/?view=graph&mode=impact&focus=s2");
    renderApp();

    expect(await screen.findByTestId("view-graph")).toBeTruthy();
    expect(useUi.getState().activeView).toBe("graph");

    await waitFor(() => {
      const [params] = paramsOf(calls, IMPACT);
      expect(params?.get("nodeId")).toBe("s2");
    });
    // 影响面模式的入口应当已被选中
    expect(screen.getByTestId("graph-mode-impact").getAttribute("aria-pressed")).toBe("true");
  });

  test("?node= 深链直接打开该符号的符号卡片", async () => {
    const { calls } = installFetch();
    window.history.replaceState(null, "", "/?view=graph&node=s2");
    renderApp();

    expect(await screen.findByTestId("view-graph")).toBeTruthy();
    await waitFor(() =>
      expect(paramsOf(calls, `/api/repos/${REPO}/symbol`).at(-1)?.get("nodeId")).toBe("s2"),
    );
    expect(screen.getByTestId("symbol-name").textContent).toBe("Session");
  });

  test("「查看语法树」切到 CST 并清掉 ?node=，同时回写 ?view=cst", async () => {
    installFetch();
    const replaceSpy = vi.spyOn(window.history, "replaceState");
    window.history.replaceState(null, "", "/?view=graph&node=s1");
    renderApp();

    await screen.findByTestId("symbol-card");
    fireEvent.click(screen.getByTestId("symbol-open-cst"));

    await waitFor(() => {
      const urls = replaceSpy.mock.calls.map((call) => String(call[2]));
      expect(urls.at(-1)).toContain("view=cst");
    });
    const last = String(replaceSpy.mock.calls.at(-1)?.[2] ?? "");
    expect(last).not.toContain("node=");
    expect(useUi.getState().activeView).toBe("cst");
    expect(useUi.getState().selectedNode).toBe("");
  });

  test("模式切换回写 ?mode=（focus 保留）", async () => {
    installFetch();
    const replaceSpy = vi.spyOn(window.history, "replaceState");
    window.history.replaceState(null, "", "/?view=graph&mode=impact&focus=s2");
    renderApp();

    await screen.findByTestId("view-graph");
    fireEvent.click(screen.getByTestId("graph-mode-dag"));

    await waitFor(() => {
      const urls = replaceSpy.mock.calls.map((call) => String(call[2]));
      expect(urls.some((url) => url.includes("mode=dag") && url.includes("focus=s2"))).toBe(true);
    });
  });
});
