import { useUi } from "../stores/ui";
import { phaseLabel } from "./JobProgress";
import { RepoInput } from "./RepoInput";
import { RepoList } from "./RepoList";

/**
 * 左侧栏：仓库录入 + 已索引仓库列表 + 当前阶段。
 * 状态一律从 store 读取，App 不再通过 props 透传。
 */
export function Sidebar() {
  const jobProgress = useUi((s) => s.jobProgress);

  return (
    <aside className="flex w-72 shrink-0 flex-col border-r border-zinc-800 bg-zinc-900/30">
      <RepoInput />

      <div className="min-h-0 flex-1 overflow-auto">
        <h3 className="px-3 pt-3 text-xs font-medium text-zinc-400">已索引仓库</h3>
        <RepoList />
      </div>

      <footer className="border-t border-zinc-800 p-3 text-[11px] text-zinc-600">
        <div data-testid="sidebar-phase">阶段：{phaseLabel(jobProgress?.phase)}</div>
        <div className="mt-1">只监听 127.0.0.1 · 不执行被测代码 · 解析跑在独立进程</div>
      </footer>
    </aside>
  );
}
