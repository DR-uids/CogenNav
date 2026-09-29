import { create } from "zustand";

import { readDeepLinkParam } from "../lib/deepLink";

/**
 * 界面语言：`en`（默认）/ `zh`。
 *
 * 选择优先级：URL 深链 `?lang=` → localStorage → 默认英文。
 * 刻意**不**跟随浏览器语言：产品要求默认英文，跟随 `navigator.language`
 * 会让中文系统上的首屏变成中文。
 */
export type Locale = "en" | "zh";

export const LOCALES: readonly Locale[] = ["en", "zh"] as const;
export const DEFAULT_LOCALE: Locale = "en";
export const LOCALE_STORAGE_KEY = "cogen.locale";

export function isLocale(value: unknown): value is Locale {
  return value === "en" || value === "zh";
}

function readStoredLocale(): Locale | null {
  try {
    const raw = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    return isLocale(raw) ? raw : null;
  } catch {
    // 隐私模式/禁用存储：当作没有存过，不影响切换（只是刷新后回到默认）。
    return null;
  }
}

function writeStoredLocale(locale: Locale): void {
  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // 同上：存不下就算了，本次会话内仍然生效。
  }
}

/** 初始语言（模块加载时算一次，用于首屏，避免先渲染英文再闪成中文）。 */
export function detectLocale(): Locale {
  const fromUrl = readDeepLinkParam("lang");
  if (isLocale(fromUrl)) return fromUrl;
  return readStoredLocale() ?? DEFAULT_LOCALE;
}

export type LocaleState = {
  locale: Locale;
  setLocale: (locale: Locale) => void;
};

export const useLocaleStore = create<LocaleState>((set) => ({
  locale: detectLocale(),
  setLocale: (locale) => {
    if (!isLocale(locale)) return;
    writeStoredLocale(locale);
    set({ locale });
  },
}));

/** 非组件的模块（api/lib）读取当前语言用。 */
export function currentLocale(): Locale {
  return useLocaleStore.getState().locale;
}
