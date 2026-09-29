import { t, useT, type MessageKey } from "../i18n";
import { useUi, type JobProgressInfo } from "../stores/ui";

/** 阶段 → 文案键：与后端 phase 一一对应。 */
const PHASE_KEYS: Record<string, MessageKey> = {
  resolve: "job.phase.resolve",
  clone: "job.phase.clone",
  walk: "job.phase.walk",
  parse: "job.phase.parse",
  extract: "job.phase.extract",
  build: "job.phase.build",
  analyze: "job.phase.analyze",
  name: "job.phase.name",
  done: "job.phase.done",
};

const STATE_KEYS: Record<string, MessageKey> = {
  queued: "job.state.queued",
  running: "job.state.running",
  done: "job.state.done",
  error: "job.state.error",
};

/**
 * 阶段标签（resolve → 解析目标 / Resolving target …）；未知阶段原样显示。
 * 走模块级 `t()`：这两个函数在组件树之外（RepoList、DirTreeView）也会被调用。
 */
export function phaseLabel(phase: string | null | undefined): string {
  if (!phase) return t("job.idle");
  const key = PHASE_KEYS[phase];
  return key ? t(key) : phase;
}

export function stateLabel(state: string | null | undefined): string {
  if (!state) return t("job.idle");
  const key = STATE_KEYS[state];
  return key ? t(key) : state;
}

/** 后端可能只给 progress 比率或只给 current/total，二者取其一推算百分比；终态恒为 100%。 */
function percentOf(info: JobProgressInfo): number {
  if (info.state === "done") return 100;
  const ratio = info.total > 0 ? info.current / info.total : info.progress;
  if (!Number.isFinite(ratio)) return 0;
  return Math.max(0, Math.min(100, Math.round(ratio * 100)));
}

/** 索引进度条：阶段标签 + 百分比 + 当前文件/消息；error 态整块转红。 */
export function JobProgress() {
  const t = useT();
  const jobId = useUi((s) => s.jobId);
  const progress = useUi((s) => s.jobProgress);

  if (!jobId) return null;

  const isError = progress?.state === "error";
  const percent = progress ? percentOf(progress) : 0;
  const frame = isError
    ? "border-rose-900/60 bg-rose-950/30"
    : "border-zinc-800 bg-zinc-900/40";
  const barColor = isError ? "bg-rose-500" : progress?.state === "done" ? "bg-emerald-500" : "bg-sky-500";

  return (
    <div
      data-testid="job-progress"
      data-state={progress?.state ?? "queued"}
      className={`shrink-0 border-b px-4 py-2 ${frame}`}
    >
      <div className="flex items-center justify-between gap-3 text-xs">
        <span className="flex items-center gap-2">
          <span
            data-testid="job-progress-phase"
            className={isError ? "font-medium text-rose-300" : "font-medium text-zinc-200"}
          >
            {progress ? phaseLabel(progress.phase) : t("job.waiting")}
          </span>
          {progress && (
            <span className="text-[11px] text-zinc-500">{stateLabel(progress.state)}</span>
          )}
        </span>
        <span
          data-testid="job-progress-percent"
          className={`font-mono text-[11px] ${isError ? "text-rose-300" : "text-zinc-400"}`}
        >
          {percent}%
        </span>
      </div>

      <div
        className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-zinc-800"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={percent}
        aria-label={t("job.progressAria")}
        data-testid="job-progress-bar"
      >
        <div
          className={`h-full rounded-full transition-[width] duration-200 ${barColor}`}
          style={{ width: `${percent}%` }}
        />
      </div>

      <div className="mt-1.5 flex items-baseline justify-between gap-3 text-[11px]">
        <span
          data-testid="job-progress-message"
          className={`min-w-0 flex-1 truncate ${isError ? "text-rose-300" : "text-zinc-400"}`}
          title={progress?.file ?? progress?.message ?? undefined}
        >
          {isError
            ? (progress?.message || t("job.failed"))
            : (progress?.file || progress?.message || t("job.awaitingProgress"))}
        </span>
        {progress && progress.total > 0 && (
          <span data-testid="job-progress-count" className="shrink-0 font-mono text-zinc-500">
            {progress.current}/{progress.total}
          </span>
        )}
      </div>
    </div>
  );
}
