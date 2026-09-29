import type { ViewId } from "../stores/ui";

export type ViewMeta = {
  id: ViewId;
  label: string;
  milestone: string;
  blurb: string;
};

/** 四个主视图的元信息：标签、交付里程碑与一句话说明。 */
export const VIEWS: readonly ViewMeta[] = [
  {
    id: "tree",
    label: "目录树",
    milestone: "M3",
    blurb: "目录树 + Treemap：按 LOC 分面积、按语言着色，顶部语言分布条可切文件数/LOC 口径。",
  },
  {
    id: "cst",
    label: "CST 语法树",
    milestone: "M2",
    blurb: "tree-sitter CST 惰性下钻：节点类型、字段名、行列范围，与右侧源码高亮双向联动。",
  },
  {
    id: "graph",
    label: "知识图谱",
    milestone: "M3",
    blurb: "三种模式：Sigma(WebGL) 力导向全图、React Flow + dagre 分层 DAG、影响面下游闭包。",
  },
  {
    id: "ask",
    label: "AI 问答",
    milestone: "M4",
    blurb: "对图谱提问（谁调用了 X / 改动 X 影响什么），答案带可跳转的符号引用。",
  },
] as const;

export function viewMeta(id: ViewId): ViewMeta {
  const found = VIEWS.find((v) => v.id === id);
  if (!found) throw new Error(`未知视图: ${id}`);
  return found;
}

/** 深链 `?view=` 的取值校验（非法值忽略，退回默认视图）。 */
export function isViewId(value: string | null | undefined): value is ViewId {
  return VIEWS.some((view) => view.id === value);
}
