/**
 * CST 行列范围 → 渲染所需位置的换算。
 *
 * 为什么需要换算：tree-sitter 的列是 **UTF-8 字节偏移**，而 shiki（以及 JS 字符串下标）
 * 用的是 **UTF-16 code unit**。对纯 ASCII 两者相同，一旦出现中文/emoji 就会错位，
 * 于是统一在这里做一次「字节列 → 字符列」的裁剪换算。
 *
 * 拆行规则刻意与 shiki 的 `splitLines(code, true)` 保持一致（保留行尾换行符），
 * 否则行号会与 shiki 渲染出来的行元素对不上。
 */

/** 节点范围：`[行, 列]` 均 0 起，列按字节计，end 为开区间。 */
export type CstRange = {
  start: readonly [number, number];
  end: readonly [number, number];
};

/** shiki 装饰器能接受的最小结构（只用到我们需要的字段）。 */
export type ShikiDecoration = {
  start: { line: number; character: number };
  end: { line: number; character: number };
  tagName: string;
  properties: Record<string, unknown>;
};

/** 高亮块类名：shiki 装饰与纯文本回退都用它，测试按它取元素。 */
export const SOURCE_MARK_CLASS = "cst-source-mark";

/** 与 shiki 一致地拆行：保留行尾的 `\r\n` / `\r` / `\n`（最后一行没有行尾）。 */
export function splitSourceLines(code: string): string[] {
  const parts = code.split(/(\r\n|\r|\n)/);
  const lines: string[] = [];
  for (let i = 0; i < parts.length; i += 2) {
    lines.push(parts[i] + (parts[i + 1] ?? ""));
  }
  return lines;
}

/** 去掉行尾换行符，供逐行渲染使用。 */
export function stripLineEnding(line: string): string {
  return line.replace(/\r?\n$/, "").replace(/\r$/, "");
}

function utf8Length(codePoint: number): number {
  if (codePoint < 0x80) return 1;
  if (codePoint < 0x800) return 2;
  if (codePoint < 0x10000) return 3;
  return 4;
}

/**
 * 字节列 → 字符列（UTF-16 code unit 数）。
 * 落在多字节字符中间时退回到该字符的起点（保证不会切出半个字符），越界则裁剪到行尾。
 */
export function byteColumnToCharColumn(lineText: string, byteColumn: number): number {
  if (!Number.isFinite(byteColumn) || byteColumn <= 0) return 0;
  let bytes = 0;
  let chars = 0;
  while (chars < lineText.length) {
    const codePoint = lineText.codePointAt(chars);
    if (codePoint === undefined) break;
    const width = utf8Length(codePoint);
    if (bytes + width > byteColumn) break;
    bytes += width;
    chars += codePoint > 0xffff ? 2 : 1;
  }
  return chars;
}

function clampLine(line: number, lineCount: number): number {
  if (!Number.isFinite(line) || line < 0) return 0;
  return Math.min(Math.trunc(line), lineCount - 1);
}

/**
 * 把 CST 范围转成 shiki 的 decorations。
 *
 * 所有越界都会被裁剪（shiki 遇到非法 position / 相交装饰会抛错，
 * 虽然上层已兜底回退，但能提前避免就避免）；空行上的 0 宽范围直接跳过。
 */
export function toShikiDecorations(code: string, ranges: readonly CstRange[]): ShikiDecoration[] {
  if (!code || ranges.length === 0) return [];
  const lines = splitSourceLines(code);
  if (lines.length === 0) return [];

  const decorations: ShikiDecoration[] = [];
  for (const range of ranges) {
    const startLine = clampLine(range.start[0], lines.length);
    const endLine = clampLine(range.end[0], lines.length);
    if (endLine < startLine) continue;

    const startCharacter = byteColumnToCharColumn(lines[startLine], range.start[1]);
    let end = { line: endLine, character: byteColumnToCharColumn(lines[endLine], range.end[1]) };
    const start = { line: startLine, character: startCharacter };

    // 0 宽范围（缺失节点、空块）向右扩一列，否则 shiki 会生成一个不可见的空包装。
    if (end.line === start.line && end.character <= start.character) {
      const lineLength = stripLineEnding(lines[start.line]).length;
      end = { line: start.line, character: Math.min(start.character + 1, lineLength) };
      if (end.character <= start.character) continue;
    }

    decorations.push({
      start,
      end,
      tagName: "mark",
      properties: { class: SOURCE_MARK_CLASS },
    });
  }
  return decorations;
}

/** 1 起展示的范围文本，如 `12:3–12:9`（列与 tree-sitter 一致按字节计）。 */
export function formatRange(range: CstRange): string {
  const [startLine, startColumn] = range.start;
  const [endLine, endColumn] = range.end;
  return `${startLine + 1}:${startColumn + 1}–${endLine + 1}:${endColumn + 1}`;
}

/** 范围标识：作为 effect 依赖，避免每次渲染新建对象导致重复高亮。 */
export function rangeKey(range: CstRange | null | undefined): string {
  if (!range) return "";
  return `${range.start[0]}:${range.start[1]}-${range.end[0]}:${range.end[1]}`;
}
