/**
 * 深链工具：与 CstView 的做法保持一致 —— 直接读写 `window.location.search`，
 * 用 `history.replaceState` 回写（不产生历史记录），不引入路由库。
 */

/** 读取当前 URL 的查询参数（解析失败时给空表，保证 SSR/异常环境下不抛）。 */
export function readDeepLink(): URLSearchParams {
  try {
    return new URLSearchParams(window.location.search);
  } catch {
    return new URLSearchParams();
  }
}

/** 读单个参数；空串按「没有」处理（`?dir=` 与不写 `dir` 等价）。 */
export function readDeepLinkParam(name: string): string | null {
  const value = readDeepLink().get(name);
  return value && value.trim() ? value : null;
}

/**
 * 回写深链参数：值为 null/空串时删除该参数，其它参数（如 `file=`、`view=`）原样保留。
 * 视图之间各自只负责自己那几个参数，互不覆盖。
 */
export function writeDeepLink(patch: Record<string, string | null>): void {
  const params = readDeepLink();
  for (const [key, value] of Object.entries(patch)) {
    if (value === null || value === "") params.delete(key);
    else params.set(key, value);
  }
  const query = params.toString();
  const url = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
  window.history.replaceState(null, "", url);
}
