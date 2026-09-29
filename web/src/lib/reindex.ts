import { useMutation, useQueryClient } from "@tanstack/react-query";

import { createRepo, type RepoSummary } from "../api/client";
import { useUi } from "../stores/ui";

/** 重新索引只需要 target/ref：同一个 target ⇒ 同一个 repoId ⇒ 命中同一个库。 */
export type ReindexTarget = Pick<RepoSummary, "target" | "ref">;

/**
 * 重新索引一个已经建过库的仓库：重新 `POST /api/repos` 同一个 target。
 *
 * 为什么这样就够：repoId = `slug(host/owner/name)@ref-sha1(url+ref)`，同 target 必然同 id，
 * 后端因此是**增量**索引；而快照目录若被清理过，`clone_repo` 会重新克隆。
 * 也就是说这正是 410「索引快照已不存在，请重新索引」唯一有用的解药。
 */
export function useReindexRepo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (repo: ReindexTarget) => createRepo({ target: repo.target, ref: repo.ref }),
    onSuccess: (result) => {
      // 与 RepoInput 提交一致：选中它，并把 App 的 SSE 订阅切到这个新任务上。
      const ui = useUi.getState();
      ui.setRepoId(result.repoId);
      ui.setJobId(result.jobId);
      ui.setJobProgress(null);
      void queryClient.invalidateQueries({ queryKey: ["repos"] });
    },
  });
}
