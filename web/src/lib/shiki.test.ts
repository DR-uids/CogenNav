import { afterEach, describe, expect, test, vi } from "vitest";

import {
  HIGHLIGHT_TIMEOUT_MS,
  MAX_HIGHLIGHT_CHARS,
  SHIKI_THEME,
  highlightToHtml,
  setShikiLoader,
  shikiLangId,
  type ShikiHighlighterLike,
} from "./shiki";

type Fake = {
  highlighter: ShikiHighlighterLike;
  calls: { code: string; options: Record<string, unknown> }[];
  /** 每次 loadLanguage 收到的加载器（函数） */
  loaders: unknown[];
};

function createFake(overrides: Partial<ShikiHighlighterLike> = {}): Fake {
  const calls: Fake["calls"] = [];
  const loaders: unknown[] = [];
  const highlighter: ShikiHighlighterLike = {
    loadLanguage: (...langs: unknown[]) => {
      loaders.push(...langs);
      return Promise.resolve();
    },
    codeToHtml: (code, options) => {
      calls.push({ code, options: options as unknown as Record<string, unknown> });
      return `<pre class="shiki">${code}</pre>`;
    },
    ...overrides,
  };
  return { highlighter, calls, loaders };
}

/** 任务要求支持的全部语言 id（text 不需要语法模块）。 */
const SUPPORTED_IDS = [
  "python",
  "javascript",
  "typescript",
  "tsx",
  "go",
  "java",
  "rust",
  "c",
  "cpp",
  "c_sharp",
  "ruby",
  "php",
  "kotlin",
  "swift",
  "bash",
  "json",
  "yaml",
  "html",
  "css",
  "markdown",
  "toml",
  "ini",
  "sql",
  "xml",
  "dockerfile",
  "make",
];

afterEach(() => {
  setShikiLoader(null);
  vi.useRealTimers();
});

describe("shikiLangId", () => {
  test("按映射表转换内部语言 id", () => {
    expect(shikiLangId("python")).toBe("python");
    expect(shikiLangId("c_sharp")).toBe("csharp");
    expect(shikiLangId("bash")).toBe("shellscript");
    expect(shikiLangId("dockerfile")).toBe("docker");
    expect(shikiLangId("make")).toBe("make");
    expect(shikiLangId("text")).toBe("text");
  });

  test("大小写/空值/未知语言一律退化", () => {
    expect(shikiLangId("Python")).toBe("python");
    expect(shikiLangId("  RUST ")).toBe("rust");
    expect(shikiLangId("brainfuck")).toBe("text");
    expect(shikiLangId(null)).toBe("text");
    expect(shikiLangId(undefined)).toBe("text");
    expect(shikiLangId("")).toBe("text");
  });
});

describe("highlightToHtml", () => {
  test("成功时按映射后的语言与主题渲染，并把范围转成 decorations", async () => {
    const fake = createFake();
    setShikiLoader(async () => fake.highlighter);

    const html = await highlightToHtml("print(1)\n", "python", [{ start: [0, 0], end: [0, 5] }]);

    expect(html).toContain("print(1)");
    expect(fake.calls).toHaveLength(1);
    expect(fake.calls[0].options.lang).toBe("python");
    expect(fake.calls[0].options.theme).toBe(SHIKI_THEME);
    expect(fake.calls[0].options.decorations).toEqual([
      {
        start: { line: 0, character: 0 },
        end: { line: 0, character: 5 },
        tagName: "mark",
        properties: { class: "cst-source-mark" },
      },
    ]);
    // 语法按需 import
    expect(fake.loaders).toHaveLength(1);
    expect(typeof fake.loaders[0]).toBe("function");
  });

  test("没有范围时不下发 decorations 字段", async () => {
    const fake = createFake();
    setShikiLoader(async () => fake.highlighter);

    await highlightToHtml("x = 1", "python");

    expect(fake.calls[0].options.decorations).toBeUndefined();
  });

  test("高亮器与语法只加载一次（同一语言多次调用复用）", async () => {
    const fake = createFake();
    const load = vi.fn(async () => fake.highlighter);
    setShikiLoader(load);

    await highlightToHtml("a", "python");
    await highlightToHtml("b", "python");

    expect(load).toHaveBeenCalledTimes(1);
    expect(fake.loaders).toHaveLength(1);
    expect(fake.calls).toHaveLength(2);
  });

  test("未知语言按 text 处理且不加载语法", async () => {
    const fake = createFake();
    setShikiLoader(async () => fake.highlighter);

    const html = await highlightToHtml("plain", "brainfuck");

    expect(html).toContain("plain");
    expect(fake.calls[0].options.lang).toBe("text");
    expect(fake.loaders).toHaveLength(0);
  });

  test("映射表里每种语言都有按需加载的语法模块", async () => {
    const fake = createFake();
    setShikiLoader(async () => fake.highlighter);

    for (const id of SUPPORTED_IDS) {
      await highlightToHtml("x", id);
    }

    expect(fake.loaders).toHaveLength(SUPPORTED_IDS.length);
  });

  test("加载器失败（wasm 不可用）返回 null 且不抛，下次调用会重试", async () => {
    const load = vi.fn(async () => {
      throw new Error("wasm 加载失败");
    });
    setShikiLoader(load);

    await expect(highlightToHtml("x", "python")).resolves.toBeNull();
    await expect(highlightToHtml("x", "python")).resolves.toBeNull();
    // 失败不缓存：允许下一次重试
    expect(load).toHaveBeenCalledTimes(2);
  });

  test("语法 import 失败返回 null", async () => {
    const fake = createFake({
      loadLanguage: () => Promise.reject(new Error("该语言没有可用语法")),
    });
    setShikiLoader(async () => fake.highlighter);

    await expect(highlightToHtml("x", "python")).resolves.toBeNull();
  });

  test("shiki 内部报错返回 null", async () => {
    const fake = createFake({
      codeToHtml: () => {
        throw new Error("Theme `github-dark` not found");
      },
    });
    setShikiLoader(async () => fake.highlighter);

    await expect(highlightToHtml("x", "python")).resolves.toBeNull();
  });

  test("空源码或超长源码直接跳过高亮", async () => {
    const fake = createFake();
    setShikiLoader(async () => fake.highlighter);

    await expect(highlightToHtml("", "python")).resolves.toBeNull();
    const huge = "a".repeat(MAX_HIGHLIGHT_CHARS + 1);
    await expect(highlightToHtml(huge, "python")).resolves.toBeNull();
    expect(fake.calls).toHaveLength(0);
  });

  test("加载超时返回 null（不阻塞视图）", async () => {
    vi.useFakeTimers();
    setShikiLoader(() => new Promise<ShikiHighlighterLike>(() => {}));

    const pending = highlightToHtml("x", "python");
    await vi.advanceTimersByTimeAsync(HIGHLIGHT_TIMEOUT_MS + 10);

    await expect(pending).resolves.toBeNull();
  });
});
