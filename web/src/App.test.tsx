import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, test, vi } from "vitest";

import App from "./App";
import { t } from "./i18n";
import { VIEWS } from "./views/registry";

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
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => ({ ok: true, status: 200, json: async () => HEALTH })),
  );
});

describe("App 骨架", () => {
  test("渲染四个视图 Tab 与默认视图", () => {
    renderApp();
    for (const view of VIEWS) {
      expect(screen.getByRole("tab", { name: new RegExp(t(view.labelKey)) })).toBeTruthy();
    }
    expect(screen.getByTestId("view-tree")).toBeTruthy();
  });

  test("切换 Tab 后展示对应视图面板", () => {
    renderApp();
    fireEvent.click(screen.getByRole("tab", { name: /CST syntax tree/ }));
    expect(screen.getByTestId("view-cst")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: /Knowledge graph/ }));
    expect(screen.getByTestId("view-graph")).toBeTruthy();
  });

  test("后端健康检查成功后显示版本与 LLM 状态", async () => {
    renderApp();
    const pill = await screen.findByTestId("health-ok");
    expect(pill.textContent).toContain("v0.1.0");
    expect(pill.textContent).toContain("LLM not configured");
  });

  test("后端不可用时给出明确提示", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new Error("connection refused");
      }),
    );
    renderApp();
    expect(await screen.findByTestId("health-error")).toBeTruthy();
  });
});
