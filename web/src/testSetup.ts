/**
 * 全局测试初始化：把「有全局副作用的状态」在每个用例后复位。
 *
 * 目前只有界面语言：它会被写进 localStorage，还会改 `<html lang>` 与文档标题，
 * 不复位的话「切到中文」的用例会污染后面的用例。
 */
import { afterEach } from "vitest";

import { DEFAULT_LOCALE, LOCALE_STORAGE_KEY, useLocaleStore } from "./stores/locale";

afterEach(() => {
  useLocaleStore.setState({ locale: DEFAULT_LOCALE });
  try {
    window.localStorage.removeItem(LOCALE_STORAGE_KEY);
  } catch {
    // 测试环境没有 localStorage（或隐私模式）：无需清理。
  }
});
