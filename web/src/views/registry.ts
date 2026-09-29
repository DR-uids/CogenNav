import { t, type MessageKey } from "../i18n";
import type { ViewId } from "../stores/ui";

export type ViewMeta = {
  id: ViewId;
  /** 标签与说明都是文案键：渲染时用 `t()` 取，语言切换后自动更新。 */
  labelKey: MessageKey;
  milestone: string;
  blurbKey: MessageKey;
};

/** 四个主视图的元信息：标签、交付里程碑与一句话说明。 */
export const VIEWS: readonly ViewMeta[] = [
  {
    id: "tree",
    labelKey: "view.tree.label",
    milestone: "M3",
    blurbKey: "view.tree.blurb",
  },
  {
    id: "cst",
    labelKey: "view.cst.label",
    milestone: "M2",
    blurbKey: "view.cst.blurb",
  },
  {
    id: "graph",
    labelKey: "view.graph.label",
    milestone: "M3",
    blurbKey: "view.graph.blurb",
  },
  {
    id: "ask",
    labelKey: "view.ask.label",
    milestone: "M4",
    blurbKey: "view.ask.blurb",
  },
] as const;

export function viewMeta(id: ViewId): ViewMeta {
  const found = VIEWS.find((v) => v.id === id);
  if (!found) throw new Error(t("view.unknown", { id }));
  return found;
}

/** 深链 `?view=` 的取值校验（非法值忽略，退回默认视图）。 */
export function isViewId(value: string | null | undefined): value is ViewId {
  return VIEWS.some((view) => view.id === value);
}
