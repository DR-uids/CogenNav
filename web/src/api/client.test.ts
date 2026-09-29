import { beforeEach, describe, expect, test, vi } from "vitest";

import { apiFetch, ApiError, requestJson } from "./client";
import { DEFAULT_LOCALE, useLocaleStore } from "../stores/locale";

function stubFetch(response: Partial<Response> = { ok: true, status: 200 }) {
  const mock = vi.fn(async () => response as Response);
  vi.stubGlobal("fetch", mock);
  return mock;
}

beforeEach(() => {
  useLocaleStore.setState({ locale: DEFAULT_LOCALE });
});

describe("apiFetch 语言头", () => {
  test("默认英文界面：Accept-Language 是 en", async () => {
    const mock = stubFetch();
    await apiFetch("/api/health");

    const [, init] = mock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Headers).get("Accept-Language")).toMatch(/^en/);
  });

  test("切到中文后：Accept-Language 变成 zh", async () => {
    useLocaleStore.setState({ locale: "zh" });
    const mock = stubFetch();
    await apiFetch("/api/health");

    const [, init] = mock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Headers).get("Accept-Language")).toMatch(/^zh/);
  });

  test("不覆盖调用方自己传的头", async () => {
    const mock = stubFetch();
    await apiFetch("/api/repos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });

    const [, init] = mock.mock.calls[0] as unknown as [string, RequestInit];
    const headers = init.headers as Headers;
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(init.method).toBe("POST");
  });

  test("requestJson 非 2xx 时把后端 detail 原样抛给 UI", async () => {
    stubFetch({
      ok: false,
      status: 410,
      json: async () => ({ detail: "The index snapshot no longer exists; please re-index" }),
    } as unknown as Partial<Response>);

    await expect(requestJson("/api/repos/x/file")).rejects.toThrowError(
      new ApiError("The index snapshot no longer exists; please re-index", 410),
    );
  });
});
