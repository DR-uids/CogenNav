import { describe, expect, test } from "vitest";

import { childPath, isAncestorPath, pathSegments } from "./cstPath";
import {
  SOURCE_MARK_CLASS,
  byteColumnToCharColumn,
  formatRange,
  rangeKey,
  splitSourceLines,
  stripLineEnding,
  toShikiDecorations,
} from "./cstRange";

describe("cstPath", () => {
  test("childPath 遵循后端 nodePath 语义", () => {
    expect(childPath("", 0)).toBe("0");
    expect(childPath("", 12)).toBe("12");
    expect(childPath("0", 3)).toBe("0.3");
    expect(childPath("0.1", 0)).toBe("0.1.0");
  });

  test("pathSegments 解析路径并在非法片段处截断", () => {
    expect(pathSegments("")).toEqual([]);
    expect(pathSegments("0")).toEqual([0]);
    expect(pathSegments("0.1.2")).toEqual([0, 1, 2]);
    expect(pathSegments("0.x.1")).toEqual([0]);
    expect(pathSegments("0.1.")).toEqual([0, 1]);
    expect(pathSegments("-1")).toEqual([]);
  });

  test("isAncestorPath 只认严格祖先", () => {
    expect(isAncestorPath("", "0")).toBe(true);
    expect(isAncestorPath("", "")).toBe(false);
    expect(isAncestorPath("0", "0.1")).toBe(true);
    expect(isAncestorPath("0", "0")).toBe(false);
    expect(isAncestorPath("0", "1")).toBe(false);
    expect(isAncestorPath("0", "10.1")).toBe(false);
  });
});

describe("cstRange", () => {
  test("splitSourceLines 与 shiki 一致：保留行尾换行符", () => {
    expect(splitSourceLines("a\nb\r\nc")).toEqual(["a\n", "b\r\n", "c"]);
    expect(splitSourceLines("")).toEqual([""]);
    expect(splitSourceLines("a\n")).toEqual(["a\n", ""]);
  });

  test("stripLineEnding 只裁掉行尾", () => {
    expect(stripLineEnding("b\r\n")).toBe("b");
    expect(stripLineEnding("b\n")).toBe("b");
    expect(stripLineEnding(" b ")).toBe(" b ");
  });

  test("byteColumnToCharColumn 处理 ASCII / 中文 / emoji 与越界", () => {
    expect(byteColumnToCharColumn("abc", 0)).toBe(0);
    expect(byteColumnToCharColumn("abc", 2)).toBe(2);
    expect(byteColumnToCharColumn("abc", 99)).toBe(3);
    expect(byteColumnToCharColumn("", 5)).toBe(0);
    // 中文每字 3 字节
    expect(byteColumnToCharColumn("中文abc", 3)).toBe(1);
    expect(byteColumnToCharColumn("中文abc", 6)).toBe(2);
    // 落在多字节字符中间时退回该字符起点，不会切出半个字符
    expect(byteColumnToCharColumn("中文abc", 4)).toBe(1);
    // emoji 占 4 字节 / 2 个 UTF-16 code unit
    expect(byteColumnToCharColumn("a😀b", 1)).toBe(1);
    expect(byteColumnToCharColumn("a😀b", 2)).toBe(1);
    expect(byteColumnToCharColumn("a😀b", 5)).toBe(3);
  });

  test("toShikiDecorations 生成带类名的 mark 装饰", () => {
    const decorations = toShikiDecorations("abc\ndef\n", [
      { start: [0, 0], end: [0, 1] },
    ]);
    expect(decorations).toEqual([
      {
        start: { line: 0, character: 0 },
        end: { line: 0, character: 1 },
        tagName: "mark",
        properties: { class: SOURCE_MARK_CLASS },
      },
    ]);
  });

  test("toShikiDecorations 把字节列换成字符列", () => {
    const [decoration] = toShikiDecorations("中文x\n", [{ start: [0, 3], end: [0, 6] }]);
    expect(decoration.start).toEqual({ line: 0, character: 1 });
    expect(decoration.end).toEqual({ line: 0, character: 2 });
  });

  test("toShikiDecorations 跨行范围与越界裁剪", () => {
    const [first, second, third] = toShikiDecorations("abc\ndef\nghi", [
      { start: [0, 1], end: [2, 2] },
      { start: [8, 0], end: [9, 0] },
      { start: [5, 0], end: [5, 99] },
    ]);
    expect(first.start).toEqual({ line: 0, character: 1 });
    expect(first.end).toEqual({ line: 2, character: 2 });
    // 越界行被裁到最后一行；0 宽范围自动扩一列，保证可见
    expect(second).toEqual({
      start: { line: 2, character: 0 },
      end: { line: 2, character: 1 },
      tagName: "mark",
      properties: { class: SOURCE_MARK_CLASS },
    });
    // 列超出该行长度时裁到行尾
    expect(third).toEqual({
      start: { line: 2, character: 0 },
      end: { line: 2, character: 3 },
      tagName: "mark",
      properties: { class: SOURCE_MARK_CLASS },
    });
  });

  test("toShikiDecorations 跳过空行上的 0 宽范围与空源码", () => {
    expect(toShikiDecorations("", [{ start: [0, 0], end: [0, 1] }])).toEqual([]);
    expect(toShikiDecorations("abc\n\n", [{ start: [1, 0], end: [1, 0] }])).toEqual([]);
    // 逆序范围直接丢弃
    expect(toShikiDecorations("abc\ndef\n", [{ start: [1, 0], end: [0, 1] }])).toEqual([]);
  });

  test("formatRange / rangeKey 是 1 起展示", () => {
    expect(formatRange({ start: [11, 2], end: [11, 8] })).toBe("12:3–12:9");
    expect(rangeKey({ start: [0, 4], end: [1, 10] })).toBe("0:4-1:10");
    expect(rangeKey(null)).toBe("");
  });
});
