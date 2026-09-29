import { useT } from "../i18n";
import { LOCALES, useLocaleStore, type Locale } from "../stores/locale";

/** 按钮上显示的短标签：语言名用各自的语言写（英文界面也能一眼认出「中文」）。 */
const SHORT_LABELS: Record<Locale, string> = { en: "EN", zh: "中文" };

/**
 * 中英文切换（右上角）。
 *
 * 选择写进 localStorage（`stores/locale.ts`），刷新后保持；URL 上的 `?lang=` 也能覆盖，
 * 方便把某一个语言的链接直接分享出去。
 */
export function LocaleSwitch() {
  const locale = useLocaleStore((s) => s.locale);
  const setLocale = useLocaleStore((s) => s.setLocale);
  const t = useT();

  return (
    <div
      role="group"
      aria-label={t("locale.aria")}
      data-testid="locale-switch"
      className="flex items-center gap-0.5 rounded border border-zinc-800 p-0.5"
    >
      {LOCALES.map((item) => {
        const active = item === locale;
        return (
          <button
            key={item}
            type="button"
            data-testid={`locale-${item}`}
            aria-pressed={active}
            title={t(`locale.${item}`)}
            onClick={() => setLocale(item)}
            className={`rounded px-1.5 py-0.5 text-[10px] transition-colors ${
              active
                ? "bg-zinc-700 text-zinc-100"
                : "text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200"
            }`}
          >
            {SHORT_LABELS[item]}
          </button>
        );
      })}
    </div>
  );
}
