import { VIEWS } from "../views/registry";
import { useUi } from "../stores/ui";

/** 视图切换：同时是后续深链 `?view=` 的写入点。 */
export function ViewTabs() {
  const activeView = useUi((s) => s.activeView);
  const setActiveView = useUi((s) => s.setActiveView);

  return (
    <nav
      className="flex h-10 shrink-0 items-center gap-1 border-b border-zinc-800 bg-zinc-900/20 px-2"
      role="tablist"
      aria-label="视图切换"
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
            title={view.blurb}
            className={`rounded px-3 py-1 text-xs transition-colors ${
              active
                ? "bg-zinc-800 text-zinc-100"
                : "text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
            }`}
          >
            {view.label}
            {!active && <span className="ml-1.5 font-mono text-[10px] text-zinc-600">{view.milestone}</span>}
          </button>
        );
      })}
    </nav>
  );
}
