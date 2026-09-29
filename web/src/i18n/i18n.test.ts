import { describe, expect, test } from "vitest";

import { acceptLanguage, isLocale, translate, type MessageKey } from ".";
import { en } from "./messages.en";
import { zh } from "./messages.zh";
import { DEFAULT_LOCALE, LOCALES } from "../stores/locale";

describe("i18n 文案表", () => {
  test("默认语言是英文", () => {
    expect(DEFAULT_LOCALE).toBe("en");
    expect(LOCALES).toEqual(["en", "zh"]);
  });

  test("中英表键完全一致（少一个键就是漏译）", () => {
    expect(Object.keys(zh).sort()).toEqual(Object.keys(en).sort());
  });

  test("英文表里没有残留中文（除了语言名与示例文本）", () => {
    const allowlist = new Set(["locale.zh"]);
    for (const [key, value] of Object.entries(en)) {
      if (allowlist.has(key)) continue;
      expect(/[\u4e00-\u9fff]/.test(value), `${key} => ${value}`).toBe(false);
    }
  });

  test("每个键都有英文与中文两条", () => {
    for (const key of Object.keys(en) as MessageKey[]) {
      expect(en[key]).toBeTruthy();
      expect(zh[key]).toBeTruthy();
    }
  });

  test("插值替换所有占位符；缺参数时保留占位符", () => {
    expect(translate("en", "dirTree.loadFailed", { error: "boom" })).toBe(
      "Failed to load directory tree: boom",
    );
    expect(translate("zh", "dirTree.loadFailed", { error: "boom" })).toBe(
      "目录树加载失败：boom",
    );
    expect(translate("en", "dirTree.loadFailed")).toContain("{error}");
  });

  test("count === 1 时用 _one 单数形式", () => {
    expect(translate("en", "unit.files", { count: 1 })).toBe("1 file");
    expect(translate("en", "unit.files", { count: 3 })).toBe("3 files");
    // 中文没有单复数：两条同形
    expect(translate("zh", "unit.files", { count: 1 })).toBe("1 文件");
    expect(translate("zh", "unit.files", { count: 3 })).toBe("3 文件");
  });

  test("未知键回退成键名（方便定位漏配）", () => {
    expect(translate("en", "does.not.exist" as MessageKey)).toBe("does.not.exist");
  });

  test("Accept-Language 按语言给出可匹配的值", () => {
    expect(acceptLanguage()).toMatch(/^en/);
  });

  test("isLocale 只认受支持的语言", () => {
    expect(isLocale("en")).toBe(true);
    expect(isLocale("zh")).toBe(true);
    expect(isLocale("fr")).toBe(false);
    expect(isLocale(null)).toBe(false);
  });
});
