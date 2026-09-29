import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, type ChangeEvent, type FormEvent } from "react";

import { createRepo } from "../api/client";
import { useT } from "../i18n";
import { useUi } from "../stores/ui";

/**
 * 仓库输入框：提交 POST /api/repos，成功后把 repoId/jobId 写入 store，
 * 并让 react-query 重新拉取仓库列表（新仓库立刻出现在列表里）。
 */
export function RepoInput() {
  const t = useT();
  const queryClient = useQueryClient();
  const setRepoId = useUi((s) => s.setRepoId);
  const setJobId = useUi((s) => s.setJobId);
  const setJobProgress = useUi((s) => s.setJobProgress);
  const [target, setTarget] = useState("");
  const [localError, setLocalError] = useState<string | null>(null);

  const mutation = useMutation({
    mutationFn: (value: string) => createRepo({ target: value }),
    onSuccess: (result) => {
      setRepoId(result.repoId);
      // jobId 变化会触发 App 重建 SSE 订阅；进度先清空，等第一条事件填充。
      setJobId(result.jobId);
      setJobProgress(null);
      setTarget("");
      setLocalError(null);
      void queryClient.invalidateQueries({ queryKey: ["repos"] });
    },
  });

  // 后端 detail（如「路径不存在」「该仓库已有任务在跑」）优先展示。
  const serverError =
    mutation.error instanceof Error
      ? mutation.error.message
      : mutation.error
        ? t("repoInput.submitFailed")
        : null;
  const errorText = localError ?? serverError;

  const onChange = (event: ChangeEvent<HTMLInputElement>) => {
    setTarget(event.target.value);
    setLocalError(null);
    if (mutation.isError) mutation.reset();
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = target.trim();
    if (!value) {
      setLocalError(t("repoInput.required"));
      return;
    }
    setLocalError(null);
    mutation.mutate(value);
  };

  const pending = mutation.isPending;

  return (
    <form className="border-b border-zinc-800 p-3" onSubmit={onSubmit}>
      <label className="mb-1.5 block text-xs font-medium text-zinc-400" htmlFor="repo-target">
        {t("repoInput.label")}
      </label>
      <div className="flex gap-1.5">
        <input
          id="repo-target"
          data-testid="repo-target-input"
          value={target}
          onChange={onChange}
          disabled={pending}
          autoComplete="off"
          spellCheck={false}
          placeholder={t("repoInput.placeholder")}
          className="min-w-0 flex-1 rounded border border-zinc-800 bg-zinc-950 px-2.5 py-1.5 font-mono text-xs text-zinc-300 placeholder:text-zinc-600 disabled:cursor-not-allowed disabled:opacity-60"
        />
        <button
          type="submit"
          data-testid="repo-submit"
          disabled={pending}
          className="shrink-0 rounded border border-zinc-700 bg-zinc-800 px-2.5 py-1.5 text-xs text-zinc-100 transition-colors hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {pending ? t("repoInput.submitting") : t("repoInput.submit")}
        </button>
      </div>

      {errorText && (
        <p
          role="alert"
          data-testid="repo-input-error"
          className="mt-1.5 rounded border border-rose-900/60 bg-rose-950/40 px-2 py-1 text-[11px] leading-relaxed break-all text-rose-300"
        >
          {errorText}
        </p>
      )}

      <p className="mt-1.5 text-[11px] leading-relaxed text-zinc-600">{t("repoInput.hint")}</p>
    </form>
  );
}
