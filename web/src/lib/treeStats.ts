/** 目录树的纯计算：拉平成行、找祖先链、语言分布汇总。 */

import type { TreeNode } from "../api/client";

/** 扁平化之后的一行。 */
export type DirRow = {
  path: string;
  name: string;
  type: "dir" | "file";
  depth: number;
  node: TreeNode;
};

/**
 * 把（可能只加载了一部分子树的）目录树按「已展开集合」拉平成行数组。
 *
 * 与 CstTree 的做法一致：虚拟列表只吃扁平数组；已懒加载的子树以 `subNodes` 为准
 * （正在请求中的目录先按「没有子节点」渲染，数据到达后调用方重算）。
 */
export function buildDirRows(
  root: TreeNode,
  expanded: ReadonlySet<string>,
  subNodes: ReadonlyMap<string, TreeNode>,
): DirRow[] {
  const rows: DirRow[] = [];

  const walk = (node: TreeNode, depth: number): void => {
    rows.push({ path: node.path, name: node.name, type: node.type, depth, node });
    if (node.type !== "dir" || !expanded.has(node.path)) return;
    const children = subNodes.get(node.path)?.children ?? node.children;
    for (const child of children) walk(child, depth + 1);
  };

  walk(root, 0);
  return rows;
}

/**
 * 某个路径的祖先目录链（含根 `""`，不含自身）。
 * `src/api/client.ts` → `["", "src", "src/api"]`；`src/api` → `["", "src"]`。
 */
export function ancestorDirPaths(path: string): string[] {
  const parts = path.split("/").filter(Boolean);
  const out = [""];
  for (let i = 0; i < parts.length - 1; i += 1) {
    out.push(parts.slice(0, i + 1).join("/"));
  }
  return out;
}

/**
 * 目录是否需要懒加载子节点：目录、当前没有 children，但确实还有文件（= 被 depth 截断）。
 */
export function needsChildren(node: TreeNode): boolean {
  return node.type === "dir" && node.children.length === 0 && node.files > 0;
}

/** 深度优先收集所有文件节点。 */
export function collectFiles(root: TreeNode): TreeNode[] {
  const files: TreeNode[] = [];
  const stack: TreeNode[] = [root];
  while (stack.length > 0) {
    const node = stack.pop();
    if (!node) break;
    if (node.type === "file") files.push(node);
    for (const child of node.children) stack.push(child);
  }
  return files;
}

/** 单个语言的汇总口径。 */
export type LanguageStat = {
  language: string;
  files: number;
  loc: number;
};

/** 按语言汇总（文件数 / 行数两种口径都给，UI 自己切换展示）。 */
export function languageSummary(files: readonly TreeNode[]): LanguageStat[] {
  const map = new Map<string, LanguageStat>();
  for (const file of files) {
    const language = file.language?.trim() ? file.language : "其它";
    const stat = map.get(language) ?? { language, files: 0, loc: 0 };
    stat.files += 1;
    stat.loc += file.loc;
    map.set(language, stat);
  }
  return [...map.values()].sort((a, b) => b.loc - a.loc || b.files - a.files);
}
