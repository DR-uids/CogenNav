/**
 * 极简 i18n：没有引入 i18next 之类的运行时，只需要「键 → 文案 + 插值 + 单复数」。
 *
 * 两种取文案方式：
 *  - 组件内用 `useT()`：订阅语言变化，切换后自动重渲染；
 *  - 纯函数/模块级（api、lib）用 `t()`：每次调用读当前语言，适合错误文案这类
 *    「抛出的那一刻」才决定语言的场景。
 */
import { useCallback } from "react";

import { currentLocale, useLocaleStore, type Locale } from "../stores/locale";
import { en, type MessageKey } from "./messages.en";
import { zh } from "./messages.zh";

export type { Locale, MessageKey };
export { DEFAULT_LOCALE, LOCALES, isLocale } from "../stores/locale";

/** 插值参数：文案里的 `{name}` 会被 `params.name` 替换。 */
export type MessageParams = Record<string, string | number>;

/** 取文案的函数签名（`useT()` / `t()` 都是它）。 */
export type Translate = (key: MessageKey, params?: MessageParams) => string;

const CATALOGS: Record<Locale, Record<MessageKey, string>> = { en, zh };

/**
 * 纯函数版翻译：`count === 1` 且存在 `<key>_one` 时用单数形式，
 * 其余占位符原样保留（缺参数时不会被替换成 `undefined`）。
 */
export function translate(locale: Locale, key: MessageKey, params?: MessageParams): string {
  const catalog = CATALOGS[locale] ?? en;
  // 键不存在时退回英文表，再退回键名本身（漏配时界面上直接暴露键名，便于定位）。
  let template: string = catalog[key] ?? en[key] ?? key;
  if (params?.count === 1) {
    const singular = `${key}_one` as MessageKey;
    if (catalog[singular]) template = catalog[singular];
  }
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match,
  );
}

/** 模块级取文案（api / lib 用）：读的是「此刻」的语言。 */
export function t(key: MessageKey, params?: MessageParams): string {
  return translate(currentLocale(), key, params);
}

/** 组件内取文案：语言切换时订阅方会重渲染。 */
export function useT(): Translate {
  const locale = useLocaleStore((s) => s.locale);
  return useCallback<Translate>((key, params) => translate(locale, key, params), [locale]);
}

/**
 * 请求头 `Accept-Language`：后端据此返回对应语言的错误 `detail`。
 * 非首选语言给 q 值，方便后端按前缀匹配。
 */
export function acceptLanguage(): string {
  return currentLocale() === "zh" ? "zh-CN,zh;q=0.9,en;q=0.8" : "en-US,en;q=0.9,zh;q=0.8";
}
