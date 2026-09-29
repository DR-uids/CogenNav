/**
 * 图谱配色：社区色、类型描边色、影响面跳数色。
 *
 * 与语言色板同样的原则——**稳定**：同一个社区编号在任何模式下都是同一个颜色，
 * 图例（Legend）与节点（渲染器）因此可以直接对比，不需要额外的映射表。
 */

/** 社区调色板（12 色循环，深色底上可辨且互相区分）。 */
export const COMMUNITY_PALETTE = [
  "#60a5fa",
  "#f472b6",
  "#34d399",
  "#fbbf24",
  "#a78bfa",
  "#22d3ee",
  "#fb7185",
  "#a3e635",
  "#f97316",
  "#818cf8",
  "#2dd4bf",
  "#e879f9",
] as const;

/** 未归属社区的节点颜色。 */
export const UNGROUPED_COLOR = "#71717a";

/** 社区编号 → 颜色（负数/超大编号也会落到合法格子里）。 */
export function communityColor(community: number | null | undefined): string {
  if (typeof community !== "number" || !Number.isFinite(community)) return UNGROUPED_COLOR;
  const index = ((Math.trunc(community) % COMMUNITY_PALETTE.length) + COMMUNITY_PALETTE.length) %
    COMMUNITY_PALETTE.length;
  return COMMUNITY_PALETTE[index] as string;
}

/** 节点类型 → 描边色（sigma 没有方形/菱形程序，形状区分改用描边）。 */
const KIND_STROKE: Record<string, string> = {
  class: "#f0abfc",
  interface: "#c4b5fd",
  struct: "#c4b5fd",
  function: "#7dd3fc",
  method: "#67e8f9",
  constructor: "#67e8f9",
  variable: "#fde68a",
  constant: "#fdba74",
  module: "#a3a3a3",
  file: "#a3a3a3",
  type: "#fca5a5",
  enum: "#fca5a5",
};

export const DEFAULT_KIND_STROKE = "#52525b";

/** 节点类型 → 描边色（`class` 类符号最亮，变量最暗，扫一眼能分辨结构）。 */
export function kindStroke(kind: string | null | undefined): string {
  if (!kind) return DEFAULT_KIND_STROKE;
  return KIND_STROKE[kind.toLowerCase()] ?? DEFAULT_KIND_STROKE;
}

/** 影响面跳数色板：第 1 跳最刺眼，越远越冷。 */
export const HOP_PALETTE = [
  "#f59e0b",
  "#fbbf24",
  "#a3e635",
  "#34d399",
  "#22d3ee",
  "#60a5fa",
] as const;

/** 跳数 → 颜色（0 表示根节点自身）。 */
export function hopColor(hop: number): string {
  if (!Number.isFinite(hop) || hop <= 0) return "#f8fafc";
  const index = Math.min(Math.trunc(hop), HOP_PALETTE.length) - 1;
  return HOP_PALETTE[index] as string;
}

/**
 * 节点半径：按 degree 开方映射到 [4, 16]，避免 hub 节点大到遮住整张图。
 */
export function nodeRadius(degree: number): number {
  const safe = Number.isFinite(degree) && degree > 0 ? degree : 0;
  return 4 + Math.min(Math.sqrt(safe) * 2.2, 12);
}
