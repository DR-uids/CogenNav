/**
 * 惰性语法高亮器（shiki）。
 *
 * 设计要点：
 *  - 全部按需加载：`shiki/core` + oniguruma wasm + 语言语法 + 主题都走动态 import，
 *    首屏不会因为高亮器体积而变慢；
 *  - **任何失败都返回 null**（wasm 加载失败、语言不支持、超时、shiki 内部报错），
 *    调用方据此回退纯文本渲染 —— 高亮只是增强，绝不允许它把视图打白；
 *  - `setShikiLoader` 是给测试用的注入点，与 `api/events.ts` 的
 *    `setJobEventsFactory` 同一套思路（jsdom 里加载 wasm 既慢又不稳定）。
 */

import { toShikiDecorations, type CstRange, type ShikiDecoration } from "./cstRange";

/** 单主题：github-dark 与页面的 zinc 深色底最接近。 */
export const SHIKI_THEME = "github-dark";

/** 加载高亮器的超时上限：超过就当作不可用，回退纯文本。 */
export const HIGHLIGHT_TIMEOUT_MS = 8000;

/** 超过这个体积不做高亮（后端一般已截断，这里是最后一道保险）。 */
export const MAX_HIGHLIGHT_CHARS = 400_000;

/** 我们内部的语言 id → shiki 语言 id；未知语言一律退化为 text。 */
const SHIKI_LANG_IDS: Record<string, string> = {
  python: "python",
  javascript: "javascript",
  typescript: "typescript",
  tsx: "tsx",
  go: "go",
  java: "java",
  rust: "rust",
  c: "c",
  cpp: "cpp",
  c_sharp: "csharp",
  ruby: "ruby",
  php: "php",
  kotlin: "kotlin",
  swift: "swift",
  bash: "shellscript",
  json: "json",
  yaml: "yaml",
  html: "html",
  css: "css",
  markdown: "markdown",
  toml: "toml",
  ini: "ini",
  sql: "sql",
  xml: "xml",
  text: "text",
  dockerfile: "docker",
  make: "make",
};

/** 语言 id → 语法模块加载器；只列我们认识的语言，未命中即按 text 处理。 */
const LANG_LOADERS: Record<string, () => Promise<unknown>> = {
  python: () => import("shiki/langs/python.mjs"),
  javascript: () => import("shiki/langs/javascript.mjs"),
  typescript: () => import("shiki/langs/typescript.mjs"),
  tsx: () => import("shiki/langs/tsx.mjs"),
  go: () => import("shiki/langs/go.mjs"),
  java: () => import("shiki/langs/java.mjs"),
  rust: () => import("shiki/langs/rust.mjs"),
  c: () => import("shiki/langs/c.mjs"),
  cpp: () => import("shiki/langs/cpp.mjs"),
  csharp: () => import("shiki/langs/csharp.mjs"),
  ruby: () => import("shiki/langs/ruby.mjs"),
  php: () => import("shiki/langs/php.mjs"),
  kotlin: () => import("shiki/langs/kotlin.mjs"),
  swift: () => import("shiki/langs/swift.mjs"),
  shellscript: () => import("shiki/langs/shellscript.mjs"),
  json: () => import("shiki/langs/json.mjs"),
  yaml: () => import("shiki/langs/yaml.mjs"),
  html: () => import("shiki/langs/html.mjs"),
  css: () => import("shiki/langs/css.mjs"),
  markdown: () => import("shiki/langs/markdown.mjs"),
  toml: () => import("shiki/langs/toml.mjs"),
  ini: () => import("shiki/langs/ini.mjs"),
  sql: () => import("shiki/langs/sql.mjs"),
  xml: () => import("shiki/langs/xml.mjs"),
  docker: () => import("shiki/langs/docker.mjs"),
  make: () => import("shiki/langs/make.mjs"),
};

/** 顺带预加载的嵌入语言（html 内嵌 css/js），失败不影响主语言。 */
const EMBEDDED_LANG_IDS: Record<string, string[]> = {
  html: ["css", "javascript"],
  php: ["html", "css", "javascript"],
};

/** 只依赖用到的三个成员，便于测试替身。 */
export type ShikiHighlighterLike = {
  loadLanguage: (...langs: unknown[]) => unknown;
  codeToHtml: (
    code: string,
    options: { lang: string; theme: string; decorations?: ShikiDecoration[] },
  ) => string;
};

export type ShikiLoader = () => Promise<ShikiHighlighterLike>;

/** 内部语言 id → shiki 语言 id（导出以便单测直接校验映射表）。 */
export function shikiLangId(language: string | null | undefined): string {
  const key = (language ?? "").trim().toLowerCase();
  return SHIKI_LANG_IDS[key] ?? "text";
}

/** 真实的 shiki 加载路径：core + oniguruma(wasm) + 主题一次到位，语法按需 loadLanguage。 */
async function loadRealShiki(): Promise<ShikiHighlighterLike> {
  const [{ createHighlighterCore }, { createOnigurumaEngine }] = await Promise.all([
    import("shiki/core"),
    import("shiki/engine/oniguruma"),
  ]);

  const highlighter = await createHighlighterCore({
    themes: [import("shiki/themes/github-dark.mjs")],
    langs: [],
    engine: createOnigurumaEngine(import("shiki/wasm")),
    // 缺嵌入语言时的告警对我们没有意义（按语言按需加载是刻意的）。
    warnings: false,
  });

  return highlighter as unknown as ShikiHighlighterLike;
}

let loader: ShikiLoader = loadRealShiki;
let highlighterPromise: Promise<ShikiHighlighterLike> | null = null;
const loadedLangIds = new Set<string>();

/** 覆盖高亮器加载器（测试用）；传 null 恢复真实实现并清空缓存。 */
export function setShikiLoader(next: ShikiLoader | null): void {
  loader = next ?? loadRealShiki;
  highlighterPromise = null;
  loadedLangIds.clear();
}

/** 单例：加载失败不缓存，下一次调用会重试（网络抖动后可自愈）。 */
function getHighlighter(): Promise<ShikiHighlighterLike> {
  if (!highlighterPromise) {
    highlighterPromise = Promise.resolve()
      .then(() => loader())
      .catch((error: unknown) => {
        highlighterPromise = null;
        throw error;
      });
  }
  return highlighterPromise;
}

function withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error(`高亮超时（${ms}ms）`)), ms);
    promise.then(
      (value) => {
        clearTimeout(timer);
        resolve(value);
      },
      (error: unknown) => {
        clearTimeout(timer);
        reject(error instanceof Error ? error : new Error(String(error)));
      },
    );
  });
}

async function ensureLanguage(highlighter: ShikiHighlighterLike, langId: string): Promise<void> {
  const wanted = langId === "text" ? [] : [langId, ...(EMBEDDED_LANG_IDS[langId] ?? [])];
  for (const id of wanted) {
    if (loadedLangIds.has(id)) continue;
    const load = LANG_LOADERS[id];
    if (!load) continue;
    await highlighter.loadLanguage(load);
    loadedLangIds.add(id);
  }
}

/**
 * 把源码渲染成高亮 HTML；失败一律返回 null（调用方回退纯文本 `<pre>`）。
 *
 * @param ranges CST 范围（`[行, 字节列]`，0 起），会转成 shiki decorations 做范围高亮。
 */
export async function highlightToHtml(
  code: string,
  language: string | null | undefined,
  ranges: readonly CstRange[] = [],
): Promise<string | null> {
  try {
    if (!code || code.length > MAX_HIGHLIGHT_CHARS) return null;
    const langId = shikiLangId(language);
    const highlighter = await withTimeout(getHighlighter(), HIGHLIGHT_TIMEOUT_MS);
    await withTimeout(ensureLanguage(highlighter, langId), HIGHLIGHT_TIMEOUT_MS);

    const decorations = toShikiDecorations(code, ranges);
    return highlighter.codeToHtml(code, {
      lang: langId,
      theme: SHIKI_THEME,
      ...(decorations.length > 0 ? { decorations } : {}),
    });
  } catch {
    // 高亮是可选增强：wasm 失败、语法缺失、超时、shiki 内部报错都静默降级。
    return null;
  }
}
