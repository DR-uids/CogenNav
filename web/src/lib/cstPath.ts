/**
 * nodePath 工具。
 *
 * 后端约定：根节点路径为空串 `""`；路径 P 的第 i 个子节点路径为
 * `P === "" ? String(i) : P + "." + i`。节点路径同时是「懒展开请求参数」
 * 和「选中态标识」，因此这里只做纯字符串计算，便于单测覆盖边界。
 */

/** 由父路径与子节点下标拼出子节点路径。 */
export function childPath(parent: string, index: number): string {
  return parent === "" ? String(index) : `${parent}.${index}`;
}

/**
 * 把 nodePath 解析成从根出发的子节点下标序列。
 * 空串（根）返回 `[]`；含非法片段（负数/非数字/空片段）时返回到该片段为止的前缀，
 * 避免拼出后端无法解析的路径。
 */
export function pathSegments(nodePath: string): number[] {
  if (!nodePath) return [];
  const segments: number[] = [];
  for (const raw of nodePath.split(".")) {
    if (!/^\d+$/.test(raw)) break;
    segments.push(Number(raw));
  }
  return segments;
}

/** 路径是否为另一个路径的严格祖先（`""` 是任何非空路径的祖先）。 */
export function isAncestorPath(ancestor: string, nodePath: string): boolean {
  if (ancestor === "") return nodePath !== "";
  return nodePath.startsWith(`${ancestor}.`);
}
