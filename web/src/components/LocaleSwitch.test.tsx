import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import App from "../App";
import { LOCALE_STORAGE_KEY } from "../stores/locale";

const HEALTH = {
  status: "ok",
  version: "0.1.0",
  home: "/tmp/cogen",
  llm_configured: false,
  web_built: false,
};

function renderApp() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  window.history.replaceState(null, "", "/");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => HEALTH })),
  );
});

describe("中英文切换", () => {
  test("默认英文：首屏就是英文，且 <html lang> 是 en", () => {
    renderApp();
    expect(screen.getByText("Indexed repositories")).toBeTruthy();
    expect(screen.getByTestId("locale-en").getAttribute("aria-pressed")).toBe("true");
    expect(document.documentElement.lang).toBe("en");
  });

  test("点「中文」后整站切成中文，写进 localStorage 并回写 ?lang=zh", () => {
    renderApp();
    fireEvent.click(screen.getByTestId("locale-zh"));

    expect(screen.getByText("已索引仓库")).toBeTruthy();
    expect(screen.queryByText("Indexed repositories")).toBeNull();
    expect(window.localStorage.getItem(LOCALE_STORAGE_KEY)).toBe("zh");
    expect(document.documentElement.lang).toBe("zh-CN");
    expect(document.title).toContain("代码仓库导航");
    expect(window.location.search).toContain("lang=zh");
  });

  test("视图 Tab、进度阶段等非组件文案也跟着切", () => {
    renderApp();
    fireEvent.click(screen.getByTestId("locale-zh"));
    expect(screen.getByRole("tab", { name: /CST 语法树/ })).toBeTruthy();
    expect(screen.queryByRole("tab", { name: /CST syntax tree/ })).toBeNull();
  });

  test("再切回英文：URL 里的 lang 参数被清掉", () => {
    renderApp();
    fireEvent.click(screen.getByTestId("locale-zh"));
    fireEvent.click(screen.getByTestId("locale-en"));

    expect(screen.getByText("Indexed repositories")).toBeTruthy();
    expect(window.location.search).not.toContain("lang=");
    expect(document.documentElement.lang).toBe("en");
  });

  test("深链 ?lang=zh 直接进中文界面（store 早于 URL 就绪也能兜住）", () => {
    window.history.replaceState(null, "", "/?lang=zh&view=graph");
    renderApp();

    expect(screen.getByText("已索引仓库")).toBeTruthy();
    expect(screen.getByTestId("view-graph")).toBeTruthy();
  });

  test("?lang= 的非法值忽略，仍然是默认英文", () => {
    window.history.replaceState(null, "", "/?lang=fr");
    renderApp();
    expect(screen.getByText("Indexed repositories")).toBeTruthy();
  });
});
