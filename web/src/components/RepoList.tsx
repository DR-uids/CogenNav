import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { deleteRepo, listRepos, type RepoSummary } from "../api/client";
import { useReindexRepo } from "../lib/reindex";
import { useUi } from "../stores/ui";
import { stateLabel } from "./JobProgress";

/**
 * 从 target/rootPath 推断仓库短名：target 优先（git 地址给出的是仓库名，
 * 而 rootPath 是克隆到本地的目录，形如 `/…/repos/<repoId>`）。
 */
export function repoName(repo: RepoSummary): string {
  const raw = repo.target || repo.rootPath || repo.repoId;
  const trimmed = raw.replace(/[/\\]+$/, "");
  const base = trimmed.split(/[/\\]/).pop() ?? repo.repoId;
  return base.replace(/\.git$/, "") || repo.repoId;
}

/** 语言分布取数量最多的前 N 个（后端给的是 lang → 文件数）。 */
export function topLanguages(
  languages: Record<string, number> | undefined,
  limit = 3,
): string[] {
  if (!languages) return [];
  return Object.entries(languages)
    .sort((a, b) => b[1] - a[1])
    .slice(0, limit)
    .map(([name]) => name);
}

const STATE_TONE: Record<string, string> = {
  queued: "border-zinc-700 text-zinc-400",
  running: "border-sky-900 bg-sky-950/40 text-sky-300",
  done: "border-emerald-900 bg-emerald-950/40 text-emerald-300",
  error: "border-rose-900 bg-rose-950/40 text-rose-300",
};

function StateBadge({ state }: { state: string }) {
  return (
    <span
      data-testid={`repo-state-${state}`}
      className={`shrink-0 rounded border px-1.5 py-0.5 text-[10px] ${
        STATE_TONE[state] ?? "border-zinc-700 text-zinc-400"
      }`}
    >
      {stateLabel(state)}
    </span>
  );
}

/** 已索引仓库列表：拉 GET /api/repos，点击选中写 store.repoId，可删除后刷新。 */
export function RepoList() {
  const queryClient = useQueryClient();
  const repoId = useUi((s) => s.repoId);
  const setRepoId = useUi((s) => s.setRepoId);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["repos"],
    queryFn: ({ signal }) => listRepos(signal),
  });

  const removal = useMutation({
    mutationFn: (id: string) => deleteRepo(id),
    onSuccess: (_void, id) => {
      if (useUi.getState().repoId === id) setRepoId(null);
      void queryClient.invalidateQueries({ queryKey: ["repos"] });
    },
  });
  const reindex = useReindexRepo();

  // 后端字段缺失时按空列表处理，避免整块 UI 崩掉。
  const repos = data ?? [];

  if (isLoading) {
    return (
      <p data-testid="repo-loading" className="p-3 text-[11px] text-zinc-500">
        正在加载仓库列表…
      </p>
    );
  }

  if (isError) {
    return (
      <p
        data-testid="repo-list-error"
        className="m-3 rounded border border-rose-900/60 bg-rose-950/30 p-2 text-[11px] break-all text-rose-300"
      >
        {error instanceof Error ? error.message : "仓库列表加载失败"}
      </p>
    );
  }

  if (repos.length === 0) {
    return (
      <p
        data-testid="repo-empty"
        className="m-3 rounded border border-dashed border-zinc-800 p-3 text-[11px] leading-relaxed text-zinc-600"
      >
        还没有索引任何仓库。
      </p>
    );
  }

  return (
    <div className="p-3">
      {removal.error && (
        <p data-testid="repo-delete-error" className="mb-2 text-[11px] break-all text-rose-300">
          {removal.error instanceof Error ? removal.error.message : "删除失败"}
        </p>
      )}
      {reindex.error && (
        <p data-testid="repo-reindex-error" className="mb-2 text-[11px] break-all text-rose-300">
          {reindex.error instanceof Error ? reindex.error.message : "重新索引失败"}
        </p>
      )}
      <ul className="space-y-1.5">
        {repos.map((repo) => {
          const name = repoName(repo);
          const active = repo.repoId === repoId;
          const langs = topLanguages(repo.languages);
          return (
            <li
              key={repo.repoId}
              data-testid="repo-card"
              className={`flex items-stretch gap-1 rounded border ${
                active ? "border-zinc-600 bg-zinc-800/70" : "border-zinc-800 bg-zinc-900/40"
              }`}
            >
              <button
                type="button"
                data-testid={`repo-select-${repo.repoId}`}
                onClick={() => setRepoId(repo.repoId)}
                title={repo.target}
                className="min-w-0 flex-1 px-2 py-1.5 text-left"
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-xs text-zinc-200">{name}</span>
                  <StateBadge state={repo.state} />
                </span>
                <span className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px] text-zinc-500">
                  <span data-testid="repo-file-count">{repo.fileCount} 文件</span>
                  <span>{repo.loc} 行</span>
                </span>
                {langs.length > 0 && (
                  <span className="mt-1 flex flex-wrap gap-1">
                    {langs.map((lang) => (
                      <span
                        key={lang}
                        data-testid="repo-language"
                        className="rounded bg-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400"
                      >
                        {lang}
                      </span>
                    ))}
                  </span>
                )}
              </button>
              <button
                type="button"
                data-testid={`repo-reindex-${repo.repoId}`}
                aria-label={`重新索引仓库 ${name}`}
                title="用同一个地址再跑一次索引（快照被清理过时会重新克隆）"
                disabled={reindex.isPending || repo.state === "queued" || repo.state === "running"}
                onClick={() => reindex.mutate({ target: repo.target, ref: repo.ref })}
                className="shrink-0 border-l border-zinc-800 px-2 text-xs text-zinc-500 transition-colors hover:bg-zinc-800 hover:text-zinc-200 disabled:cursor-not-allowed disabled:opacity-40"
              >
                重新索引
              </button>
              <button
                type="button"
                data-testid={`repo-delete-${repo.repoId}`}
                aria-label={`删除仓库 ${name}`}
                disabled={removal.isPending}
                onClick={() => removal.mutate(repo.repoId)}
                className="shrink-0 border-l border-zinc-800 px-2 text-xs text-zinc-500 transition-colors hover:bg-rose-950/40 hover:text-rose-300 disabled:cursor-not-allowed disabled:opacity-50"
              >
                删除
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
