/**
 * Treemap 布局：用 `d3-hierarchy` 的 `treemap()` 做**纯计算**，UI 自己画 `<rect>`。
 *
 * 为什么不用 d3 的 DOM 生成器：只依赖 d3 的算法部分，渲染交给 React（可测试、
 * 无第三方 DOM 副作用）；坐标算完后是一组普通对象，单元测试/组件测试都能直接断言。
 *
 * 面积口径：叶子（文件）按 `loc` 分配（loc 为 0 的文件给 1，保证仍可见），
 * 目录的面积是其子树之和（这是 treemap 的自然语义）。
 */

import { hierarchy, treemap } from "d3-hierarchy";

import type { TreeNode } from "../api/client";

/** 一个矩形：`x/y` 为左上角，`width/height` 已保证非负。 */
export type TreemapRect = {
  path: string;
  name: string;
  type: "dir" | "file";
  depth: number;
  language: string | null;
  loc: number;
  files: number;
  symbols: number;
  x: number;
  y: number;
  width: number;
  height: number;
};

/** 布局画布的逻辑尺寸；UI 用一个 viewBox 等比缩放，避免依赖真实像素尺寸。 */
export const TREEMAP_WIDTH = 1000;
export const TREEMAP_HEIGHT = 640;

/**
 * 计算矩形（含目录节点；UI 按 type 决定填充/描边）。
 * 整棵树 loc 全为 0（空仓库）时返回空数组，由调用方走空态。
 */
export function layoutTreemap(
  root: TreeNode,
  width: number = TREEMAP_WIDTH,
  height: number = TREEMAP_HEIGHT,
): TreemapRect[] {
  const rootNode = hierarchy<TreeNode>(root, (node) => node.children).sum((node) =>
    node.type === "file" ? Math.max(node.loc, 1) : 0,
  );
  if (!rootNode.value || rootNode.value <= 0) return [];

  const laid = treemap<TreeNode>()
    .size([width, height])
    .paddingInner(2)
    .paddingOuter(3)
    .round(true)(
    rootNode.sort((a, b) => (b.value ?? 0) - (a.value ?? 0)),
  );

  const rects: TreemapRect[] = [];
  laid.each((node) => {
    const data = node.data;
    rects.push({
      path: data.path,
      name: data.name,
      type: data.type,
      depth: node.depth,
      language: data.language,
      loc: data.loc,
      files: data.files,
      symbols: data.symbols,
      x: node.x0,
      y: node.y0,
      width: Math.max(node.x1 - node.x0, 0),
      height: Math.max(node.y1 - node.y0, 0),
    });
  });
  return rects;
}
