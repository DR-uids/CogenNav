/** 图谱过滤面板的纯逻辑：复选列表用「隐式全选」语义（空数组 = 全部通过）。 */

import { CONFIDENCE_LEVELS } from "../api/graph";

/** 是否勾选：列表为空表示「全部勾选」。 */
export function isChecked(list: readonly string[], value: string): boolean {
  return list.length === 0 || list.includes(value);
}

/**
 * 勾选/取消勾选。全部选中时归一化成空数组（← 这样 queryKey 与请求参数只取决于
 * 「有没有在过滤」，不会出现「全选」与「不传参数」两种等价状态各占一个缓存」。
 */
export function toggleChecked(
  list: readonly string[],
  all: readonly string[],
  value: string,
): string[] {
  const current = list.length === 0 ? [...all] : [...list];
  const next = current.includes(value)
    ? current.filter((item) => item !== value)
    : [...current, value];
  if (next.length === 0) return []; // 一个都不选 = 不额外过滤（避免出现空结果的无意义状态）
  return next.length === all.length ? [] : next;
}

/** 发给后端的 kinds / relations 白名单：空数组 → undefined（不传参数 = 不过滤）。 */
export function whitelist(list: readonly string[]): string[] | undefined {
  return list.length > 0 ? [...list] : undefined;
}

/**
 * 置信度白名单：`hideAmbiguous` 默认打开（AMBIGUOUS 是「猜的调用」，默认不进主视图）。
 * 返回 undefined 表示「不需要传 confidence 参数」（等于全都要）。
 */
export function confidenceWhitelist(
  selected: readonly string[],
  hideAmbiguous: boolean,
): string[] | undefined {
  const base = selected.length > 0 ? [...selected] : [...CONFIDENCE_LEVELS];
  const effective = hideAmbiguous ? base.filter((item) => item !== "ambiguous") : base;
  if (effective.length === CONFIDENCE_LEVELS.length) return undefined;
  return effective;
}
