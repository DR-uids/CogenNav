/**
 * 真实 shiki 管线的集成测试（不注入假加载器）。
 *
 * shiki.test.ts 覆盖的是「失败如何降级」，这里确认真实环境下
 * oniguruma wasm、主题名、按需语法与 decorations 确实可用 —— 
 * 即浏览器里真的会高亮，而不是永远走纯文本回退。
 */
import { describe, expect, test } from "vitest";

import { highlightToHtml } from "./shiki";

function marks(html: string): string[] {
  return [...html.matchAll(/<mark[^>]*class="cst-source-mark"[^>]*>(.*?)<\/mark>/g)].map(
    (match) => match[1],
  );
}

describe("shiki 真实管线（集成）", () => {
  test("python 高亮 + 范围装饰可用", async () => {
    const html = await highlightToHtml("def f():\n    return 1\n", "python", [
      { start: [0, 4], end: [0, 5] },
    ]);

    expect(html).not.toBeNull();
    expect(html).toContain('<pre class="shiki github-dark"');
    // 关键字确实被 tokenize（带内联颜色），且范围标记落在 identifier 上
    expect(html).toMatch(/color:#F97583">def/);
    expect(marks(html as string)).toEqual(["f"]);
  }, 30_000);

  test("中文行按字节列换算后标记不偏移", async () => {
    // 「名字」占 6 字节；CST 给的是字节列，shiki 需要的是字符列
    const html = await highlightToHtml("名字 = 1\n", "python", [{ start: [0, 0], end: [0, 6] }]);

    expect(html).not.toBeNull();
    expect(marks(html as string)).toEqual(["名字"]);
  }, 30_000);

  test("未知语言退化为纯文本但仍返回 HTML", async () => {
    const html = await highlightToHtml("plain text", "unknown-lang");

    expect(html).not.toBeNull();
    expect(html).toContain("plain text");
  }, 30_000);
});
