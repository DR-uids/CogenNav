/**
 * 语言配色：目录树视图的语言分布条与 Treemap 共用一份「稳定色板」。
 *
 * 稳定性要求：同一语言在两次渲染、两棵树里必须是同一个颜色，因此
 *  - 常见语言写死映射（避免哈希撞色导致 python / perl 同色）；
 *  - 未知语言用字符串哈希落到固定调色板上（同一语言永远同一格）。
 */

import { t } from "../i18n";

/** 常见语言的固定色（深色底上可辨）。 */
const LANGUAGE_COLORS: Record<string, string> = {
  python: "#3b82f6",
  typescript: "#38bdf8",
  javascript: "#facc15",
  tsx: "#22d3ee",
  jsx: "#fde047",
  java: "#f97316",
  kotlin: "#c084fc",
  scala: "#f472b6",
  go: "#2dd4bf",
  rust: "#fb7185",
  c: "#a1a1aa",
  cpp: "#f87171",
  csharp: "#a78bfa",
  ruby: "#ef4444",
  php: "#818cf8",
  swift: "#fb923c",
  dart: "#0ea5e9",
  elixir: "#e879f9",
  haskell: "#a78bfa",
  lua: "#6366f1",
  perl: "#94a3b8",
  r: "#2563eb",
  shell: "#4ade80",
  bash: "#4ade80",
  zsh: "#22c55e",
  powershell: "#60a5fa",
  make: "#a3a3a3",
  makefile: "#a3a3a3",
  dockerfile: "#38bdf8",
  yaml: "#cbd5e1",
  toml: "#94a3b8",
  json: "#e2e8f0",
  xml: "#cbd5e1",
  html: "#fdba74",
  css: "#7dd3fc",
  scss: "#f9a8d4",
  vue: "#42b883",
  svelte: "#ff5722",
  sql: "#fbbf24",
  markdown: "#64748b",
  text: "#52525b",
};

/** 未知语言的兜底调色板（比上面的固定色更暗，避免抢视线）。 */
const FALLBACK_PALETTE = [
  "#64748b",
  "#7c8ea3",
  "#8b7f6b",
  "#6b8b7f",
  "#8b6b8b",
  "#6b7f8b",
  "#8b8b6b",
  "#7f6b8b",
] as const;

/** 目录 / 无语言节点的中性色。 */
export const NEUTRAL_COLOR = "#3f3f46";

/** 字符串哈希（djb2 变体）：同输入永远同输出，跨会话稳定。 */
function hash(text: string): number {
  let value = 5381;
  for (let i = 0; i < text.length; i += 1) {
    value = ((value << 5) + value + text.charCodeAt(i)) | 0;
  }
  return Math.abs(value);
}

/** 语言 → 颜色（null 走中性色）。 */
export function languageColor(language: string | null | undefined): string {
  if (!language) return NEUTRAL_COLOR;
  const key = language.toLowerCase();
  const fixed = LANGUAGE_COLORS[key];
  if (fixed) return fixed;
  return FALLBACK_PALETTE[hash(key) % FALLBACK_PALETTE.length] as string;
}

/** 语言展示名：空语言统一显示成「其它」。 */
export function languageLabel(language: string | null | undefined): string {
  return language && language.trim() ? language : t("language.other");
}
