import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import type { RepoSummary } from "../api/client";
import { useUi } from "../stores/ui";
import { RepoList, repoName, topLanguages } from "./RepoList";

function jsonRes(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

const REPOS: RepoSummary[] = [
  {
    repoId: "repo-alpha",
    target: "https://github.com/acme/alpha.git",
    source: "git",
    ref: null,
    rootPath: "/tmp/cogen/repos/repo-alpha",
    state: "done",
    fileCount: 12,
    loc: 340,
    languages: { TypeScript: 10, Python: 2, Rust: 1, Go: 1 },
    indexedAt: "2026-01-01T00:00:00Z",
    message: null,
    jobId: "job-alpha",
  },
  {
    repoId: "repo-beta",
    target: "/Users/dev/code/beta",
    source: "local",
    ref: null,
    rootPath: "/Users/dev/code/beta",
    state: "running",
    fileCount: 3,
    loc: 40,
    languages: { Python: 3 },
    indexedAt: null,
    message: "遍历文件",
    jobId: "job-beta",
  },
];

function renderList() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <RepoList />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  useUi.setState({ repoId: null, jobId: null, jobProgress: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("RepoList", () => {
  test("渲染仓库卡片：名字、文件数、语言 top3、状态", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, _init?: RequestInit) => jsonRes({ repos: REPOS })));

    renderList();

    await waitFor(() => expect(screen.getAllByTestId("repo-card")).toHaveLength(2));
    expect(screen.getByText("alpha")).toBeTruthy();
    expect(screen.getByText("beta")).toBeTruthy();
    expect(screen.getAllByTestId("repo-file-count").map((el) => el.textContent)).toEqual([
      "12 文件",
      "3 文件",
    ]);
    // 语言只展示前 3 个（Go 与 Rust 同为 1，稳定排序下 Rust 在前）
    expect(screen.getAllByTestId("repo-language").map((el) => el.textContent)).toEqual([
      "TypeScript",
      "Python",
      "Rust",
      "Python",
    ]);
    expect(screen.getByTestId("repo-state-done").textContent).toBe("已完成");
    expect(screen.getByTestId("repo-state-running").textContent).toBe("进行中");
  });

  test("空列表展示空态提示", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, _init?: RequestInit) => jsonRes({ repos: [] })));

    renderList();
    expect((await screen.findByTestId("repo-empty")).textContent).toContain("还没有索引任何仓库");
  });

  test("点击卡片写入 store 的 repoId", async () => {
    vi.stubGlobal("fetch", vi.fn(async (_url: string, _init?: RequestInit) => jsonRes({ repos: REPOS })));

    renderList();
    fireEvent.click(await screen.findByTestId("repo-select-repo-beta"));
    expect(useUi.getState().repoId).toBe("repo-beta");
  });

  test("删除仓库调用 DELETE 并刷新列表、清空选中", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      if (init?.method === "DELETE") return jsonRes(null, 204);
      return jsonRes({ repos: REPOS });
    });
    vi.stubGlobal("fetch", fetchMock);
    useUi.setState({ repoId: "repo-alpha" });

    renderList();
    fireEvent.click(await screen.findByTestId("repo-delete-repo-alpha"));

    await waitFor(() => expect(useUi.getState().repoId).toBeNull());
    const deleteCall = fetchMock.mock.calls.find((c) => c[1]?.method === "DELETE");
    expect(deleteCall?.[0]).toBe("/api/repos/repo-alpha");
    // DELETE 成功后 invalidate 触发一次重新拉取
    await waitFor(() =>
      expect(fetchMock.mock.calls.filter((c) => !c[1]?.method || c[1]?.method === "GET").length).toBeGreaterThan(1),
    );
  });

  test("repoName / topLanguages 边界处理", () => {
    expect(repoName(REPOS[0])).toBe("alpha");
    expect(repoName({ ...REPOS[1], target: "" })).toBe("beta");
    expect(repoName({ ...REPOS[1], target: "/a/b/" })).toBe("b");
    // target 缺失时退回 rootPath
    expect(repoName({ ...REPOS[1], target: "", rootPath: "/x/y/gamma" })).toBe("gamma");
    expect(topLanguages(undefined)).toEqual([]);
    expect(topLanguages({ B: 1, A: 5 }, 2)).toEqual(["A", "B"]);
  });
});
