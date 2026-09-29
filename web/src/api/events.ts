/**
 * 索引任务进度流（SSE）封装。
 *
 * 为什么不直接在组件里 new EventSource：
 *  - 组件测试需要在 jsdom 里注入假实现（jsdom 不实现 EventSource）；
 *  - 订阅点集中在这里，方便统一处理「后端 error 事件」和「连接层断开」两种情况。
 */

import type { JobPhase, JobState } from "./client";

/** event: progress 的 data 结构（已归一化：数值缺失时补 0）。 */
export type JobProgressPayload = {
  phase: JobPhase;
  state: JobState;
  current: number;
  total: number;
  /** 0~1 的比率，与 current/total 二者取其一即可驱动进度条。 */
  progress: number;
  message: string;
  /** 当前正在处理的文件（walk 阶段才有）。 */
  file?: string;
};

export type JobEventHandlers = {
  onProgress?: (payload: JobProgressPayload) => void;
  onDone?: () => void;
  onError?: (message: string) => void;
};

/** 只用到 EventSource 的最小面：便于测试注入。 */
export type SseEvent = { data: string };

export type EventSourceLike = {
  addEventListener: (type: string, listener: (ev: SseEvent) => void) => void;
  close: () => void;
};

export type EventSourceFactory = (url: string) => EventSourceLike;

export type JobSubscription = {
  /** 幂等关闭：重复调用不会报错。 */
  close: () => void;
  readonly closed: boolean;
};

function nativeFactory(url: string): EventSourceLike {
  return new EventSource(url) as unknown as EventSourceLike;
}

let defaultFactory: EventSourceFactory = nativeFactory;

/**
 * 覆盖默认 EventSource 工厂（测试用）。
 * 传 null 恢复为浏览器原生 EventSource。
 */
export function setJobEventsFactory(factory: EventSourceFactory | null): void {
  defaultFactory = factory ?? nativeFactory;
}

function toNumber(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function parseJson<T>(raw: string): T | null {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

/** 把后端可能字段不全的 progress data 归一化，避免 UI 出现 NaN%。 */
export function normalizeProgress(raw: unknown): JobProgressPayload | null {
  if (!raw || typeof raw !== "object") return null;
  const data = raw as Record<string, unknown>;
  const phase = data.phase;
  const state = data.state;
  if (typeof phase !== "string" || typeof state !== "string") return null;
  const payload: JobProgressPayload = {
    phase: phase as JobPhase,
    state: state as JobState,
    current: toNumber(data.current),
    total: toNumber(data.total),
    progress: toNumber(data.progress),
    message: typeof data.message === "string" ? data.message : "",
  };
  if (typeof data.file === "string" && data.file) payload.file = data.file;
  return payload;
}

/**
 * 订阅单个索引任务的 SSE 进度。
 * 连接在 `done` 或 `error` 事件后自动关闭；组件卸载时调用返回的 close()。
 */
export function subscribeJobEvents(
  jobId: string,
  handlers: JobEventHandlers,
  factory: EventSourceFactory = defaultFactory,
): JobSubscription {
  const url = `/api/jobs/${encodeURIComponent(jobId)}/events`;
  const source = factory(url);
  let closed = false;

  const close = () => {
    if (closed) return;
    closed = true;
    source.close();
  };

  source.addEventListener("progress", (ev) => {
    if (closed) return;
    const payload = normalizeProgress(parseJson<unknown>(ev.data));
    if (payload) handlers.onProgress?.(payload);
  });

  source.addEventListener("done", () => {
    if (closed) return;
    handlers.onDone?.();
    // 终态：主动断开，避免浏览器按 EventSource 语义自动重连。
    close();
  });

  source.addEventListener("error", (ev) => {
    if (closed) return;
    const payload = parseJson<{ message?: unknown }>(ev.data);
    const message =
      typeof payload?.message === "string" && payload.message
        ? payload.message
        : "索引任务连接中断";
    handlers.onError?.(message);
    close();
  });

  return {
    close,
    get closed() {
      return closed;
    },
  };
}
