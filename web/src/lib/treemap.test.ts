import { describe, expect, test } from "vitest";

import type { TreeNode } from "../api/client";
import { layoutTreemap } from "./treemap";

function file(name: string, path: string, loc: number, language = "python"): TreeNode {
  return {
    name,
    path,
    type: "file",
    loc,
    files: 1,
    symbols: 2,
    language,
    errorCount: 0,
    children: [],
  };
}

function dir(name: string, path: string, children: TreeNode[], loc: number): TreeNode {
  const files = children.reduce((sum, child) => sum + child.files, 0);
  return {
    name,
    path,
    type: "dir",
    loc,
    files,
    symbols: 0,
    language: null,
    errorCount: 0,
    children,
  };
}

const WIDTH = 400;
const HEIGHT = 200;

describe("layoutTreemap", () => {
  test("叶子按 loc 分面积，面积比接近 loc 比", () => {
    const root = dir("root", "", [file("a.py", "a.py", 30), file("b.py", "b.py", 10)], 40);
    const rects = layoutTreemap(root, WIDTH, HEIGHT);

    const leaves = rects.filter((rect) => rect.type === "file");
    expect(leaves).toHaveLength(2);
    // 目录也在结果里（UI 用描边画），根目录覆盖整块画布
    const rootRect = rects.find((rect) => rect.path === "");
    expect(rootRect?.type).toBe("dir");
    expect(rootRect?.width).toBeGreaterThan(WIDTH - 20);

    const area = (path: string) => {
      const rect = leaves.find((item) => item.path === path);
      return (rect?.width ?? 0) * (rect?.height ?? 0);
    };
    const total = area("a.py") + area("b.py");
    expect(area("a.py")).toBeGreaterThan(area("b.py"));
    expect(area("a.py") / total).toBeGreaterThan(0.6);
    expect(area("a.py") / total).toBeLessThan(0.9);
  });

  test("所有矩形都在画布内，且目录按子树聚合（loc 为 0 的文件也给面积）", () => {
    const root = dir(
      "root",
      "",
      [
        dir("src", "src", [file("a.py", "src/a.py", 0), file("b.py", "src/b.py", 20)], 20),
        file("README.md", "README.md", 5, "markdown"),
      ],
      25,
    );
    const rects = layoutTreemap(root, WIDTH, HEIGHT);
    expect(rects.filter((rect) => rect.type === "file")).toHaveLength(3);

    for (const rect of rects) {
      expect(rect.x).toBeGreaterThanOrEqual(0);
      expect(rect.y).toBeGreaterThanOrEqual(0);
      expect(rect.x + rect.width).toBeLessThanOrEqual(WIDTH + 0.001);
      expect(rect.y + rect.height).toBeLessThanOrEqual(HEIGHT + 0.001);
    }

    // loc=0 的文件仍然有可见面积（按 1 计）
    const zero = rects.find((rect) => rect.path === "src/a.py");
    expect((zero?.width ?? 0) * (zero?.height ?? 0)).toBeGreaterThan(0);
    expect(rects.find((rect) => rect.path === "src")?.type).toBe("dir");
  });

  test("整棵树 loc 全为 0 时返回空数组（调用方走空态）", () => {
    const root = dir("root", "", [file("a.py", "a.py", 0)], 0);
    // 注意：loc 为 0 的文件按 1 计，因此这里仍有面积；真正空的是「没有文件」的仓库。
    expect(layoutTreemap(root, WIDTH, HEIGHT).length).toBeGreaterThan(0);

    const empty = dir("empty", "", [], 0);
    expect(layoutTreemap(empty, WIDTH, HEIGHT)).toEqual([]);
  });
});
