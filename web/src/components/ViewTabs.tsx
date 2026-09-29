import { useT } from "../i18n";
import { useUi } from "../stores/ui";
import { VIEWS } from "../views/registry";

/** 视图切换：同时是后续深链 `?view=` 的写入点。 */
export function ViewTabs() {
  const t = useT();
  const activeView = useUi((s) => s.activeView);
  const setActiveView = useUi((s) => s.setActiveView);

  return (
    <nav
      className="flex h-10 shrink-0 items-center gap-1 border-b border-zinc-800 bg-zinc-900/20 px-2"
      role="tablist"
      aria-label={t("viewTabs.aria")}
    >
      {VIEWS.map((view) => {
        const active = view.id === activeView;
        return (
          <button
            key={view.id}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => setActiveView(view.id)}
            title={t(view.blurbKey)}
            className={`rounded px-3 py-1 text-xs transition-colors ${
              active
                ? "bg-zinc-800 text-zinc-100"
                : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
            }`}
          >
            {t(view.labelKey)}
            {!active && <span className="ml-1.5 font-mono text-[10px] text-zinc-600">{view.milestone}</span>}
          </button>
        );
      })}
    </nav>
  );
}
