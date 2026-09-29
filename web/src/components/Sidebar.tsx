import { useT } from "../i18n";
import { useUi } from "../stores/ui";
import { phaseLabel } from "./JobProgress";
import { RepoInput } from "./RepoInput";
import { RepoList } from "./RepoList";

/**
 * 左侧栏：仓库录入 + 已索引仓库列表 + 当前阶段。
 * 状态一律从 store 读取，App 不再通过 props 透传。
 */
export function Sidebar() {
  const t = useT();
  const jobProgress = useUi((s) => s.jobProgress);

  return (
    <aside className="flex w-72 shrink-0 flex-col border-r border-zinc-800 bg-zinc-900/30">
      <RepoInput />

      <div className="min-h-0 flex-1 overflow-auto">
        <h3 className="px-3 pt-3 text-xs font-medium text-zinc-400">{t("sidebar.repos")}</h3>
        <RepoList />
      </div>

      <footer className="border-t border-zinc-800 p-3 text-[11px] text-zinc-600">
        <div data-testid="sidebar-phase">
          {t("sidebar.phase", { phase: phaseLabel(jobProgress?.phase) })}
        </div>
        <div className="mt-1">{t("sidebar.notice")}</div>
      </footer>
    </aside>
  );
}
