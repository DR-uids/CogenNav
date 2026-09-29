import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import { useUi } from "../stores/ui";
import { RepoInput } from "./RepoInput";

function jsonRes(body: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function renderInput() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const view = render(
    <QueryClientProvider client={client}>
      <RepoInput />
    </QueryClientProvider>,
  );
  return { invalidate, ...view };
}

beforeEach(() => {
  useUi.setState({ repoId: null, jobId: null, jobProgress: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("RepoInput", () => {
  test("提交成功后调用 createRepo，写入 store 并刷新仓库列表", async () => {
    const fetchMock = vi.fn(async (_url: string, _init?: RequestInit) =>
      jsonRes({ repoId: "repo-1", jobId: "job-1" }, 201),
    );
    vi.stubGlobal("fetch", fetchMock);

    const { invalidate } = renderInput();
    fireEvent.change(screen.getByTestId("repo-target-input"), {
      target: { value: "  https://github.com/acme/alpha.git  " },
    });
    fireEvent.click(screen.getByTestId("repo-submit"));

    await waitFor(() => expect(useUi.getState().jobId).toBe("job-1"));
    expect(useUi.getState().repoId).toBe("repo-1");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/repos");
    expect(init?.method).toBe("POST");
    // 前后空格应被裁剪
    expect(JSON.parse(String(init?.body))).toEqual({ target: "https://github.com/acme/alpha.git" });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["repos"] });
  });

  test("后端返回 400 时展示 detail 文案且不写 store", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, _init?: RequestInit) =>
        jsonRes({ detail: "target 路径不存在：/tmp/nope" }, 400),
      ),
    );

    renderInput();
    fireEvent.change(screen.getByTestId("repo-target-input"), { target: { value: "/tmp/nope" } });
    fireEvent.click(screen.getByTestId("repo-submit"));

    const alert = await screen.findByTestId("repo-input-error");
    expect(alert.textContent).toContain("路径不存在");
    expect(useUi.getState().repoId).toBeNull();
    expect(useUi.getState().jobId).toBeNull();
  });

  test("空输入不发请求并提示必填", () => {
    const fetchMock = vi.fn(async () => jsonRes({}, 201));
    vi.stubGlobal("fetch", fetchMock);

    renderInput();
    fireEvent.click(screen.getByTestId("repo-submit"));

    expect(screen.getByTestId("repo-input-error").textContent).toContain(
      "Enter a repository URL",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  test("提交中禁用输入框与按钮", async () => {
    let release: (value: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      release = resolve;
    });
    vi.stubGlobal("fetch", vi.fn(async (_url: string, _init?: RequestInit) => pending));

    renderInput();
    fireEvent.change(screen.getByTestId("repo-target-input"), { target: { value: "owner/repo" } });
    fireEvent.click(screen.getByTestId("repo-submit"));

    const button = await screen.findByTestId("repo-submit");
    expect((button as HTMLButtonElement).disabled).toBe(true);
    expect(button.textContent).toContain("Submitting");
    expect((screen.getByTestId("repo-target-input") as HTMLInputElement).disabled).toBe(true);

    release(jsonRes({ repoId: "r", jobId: "j" }, 201));
    await waitFor(() => expect(useUi.getState().jobId).toBe("j"));
  });
});
