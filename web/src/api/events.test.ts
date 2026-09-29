import { afterEach, describe, expect, test, vi } from "vitest";

import {
  normalizeProgress,
  setJobEventsFactory,
  subscribeJobEvents,
  type EventSourceLike,
  type SseEvent,
} from "./events";
import { useUi } from "../stores/ui";

/** 假 EventSource：记录监听器，测试里手动 emit。 */
class FakeEventSource implements EventSourceLike {
  readonly url: string;
  closed = false;
  private readonly listeners = new Map<string, ((ev: SseEvent) => void)[]>();

  constructor(url: string) {
    this.url = url;
  }

  addEventListener(type: string, listener: (ev: SseEvent) => void): void {
    const list = this.listeners.get(type) ?? [];
    list.push(listener);
    this.listeners.set(type, list);
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string, data: unknown): void {
    const payload = typeof data === "string" ? data : JSON.stringify(data);
    for (const listener of this.listeners.get(type) ?? []) listener({ data: payload });
  }

  get listenerCount(): number {
    return [...this.listeners.values()].reduce((sum, list) => sum + list.length, 0);
  }
}

function createFake() {
  const created: FakeEventSource[] = [];
  const factory = (url: string) => {
    const source = new FakeEventSource(url);
    created.push(source);
    return source;
  };
  return { created, factory };
}

afterEach(() => {
  setJobEventsFactory(null);
  useUi.setState({ repoId: null, jobId: null, jobProgress: null });
});

describe("subscribeJobEvents", () => {
  test("未显式传入工厂时使用模块级注入的工厂，并注册三类事件", () => {
    const { created, factory } = createFake();
    setJobEventsFactory(factory);

    const sub = subscribeJobEvents("job 2", {});

    expect(created).toHaveLength(1);
    expect(created[0].url).toBe("/api/jobs/job%202/events");
    expect(created[0].listenerCount).toBe(3);
    sub.close();
    expect(created[0].closed).toBe(true);
  });

  test("progress 事件归一化后回调并写入 store", () => {
    const { created, factory } = createFake();
    const setJobProgress = useUi.getState().setJobProgress;

    subscribeJobEvents(
      "job-1",
      {
        onProgress: (payload) => setJobProgress(payload),
      },
      factory,
    );

    expect(created[0].url).toBe("/api/jobs/job-1/events");
    created[0].emit("progress", {
      phase: "walk",
      state: "running",
      current: 7,
      total: 10,
      progress: 0.7,
      message: "遍历文件",
      file: "src/app.ts",
    });

    const progress = useUi.getState().jobProgress;
    expect(progress?.phase).toBe("walk");
    expect(progress?.current).toBe(7);
    expect(progress?.total).toBe(10);
    expect(progress?.file).toBe("src/app.ts");
    expect(created[0].closed).toBe(false);
  });

  test("done 事件回调后自动关闭连接，且不再处理后续事件", () => {
    const { created, factory } = createFake();
    const onProgress = vi.fn();
    const onDone = vi.fn();
    subscribeJobEvents("job-1", { onProgress, onDone }, factory);

    created[0].emit("done", { phase: "done", state: "done" });
    expect(onDone).toHaveBeenCalledTimes(1);
    expect(created[0].closed).toBe(true);

    created[0].emit("progress", { phase: "walk", state: "running" });
    expect(onProgress).not.toHaveBeenCalled();
  });

  test("error 事件带出后端 message 并关闭连接", () => {
    const { created, factory } = createFake();
    const onError = vi.fn();
    subscribeJobEvents("job-1", { onError }, factory);

    created[0].emit("error", { state: "error", message: "clone 失败：仓库不存在" });
    expect(onError).toHaveBeenCalledWith("clone 失败：仓库不存在");
    expect(created[0].closed).toBe(true);
  });

  test("连接层 error（无 data）退化为默认文案", () => {
    const { created, factory } = createFake();
    const onError = vi.fn();
    subscribeJobEvents("job-1", { onError }, factory);

    created[0].emit("error", "");
    expect(onError).toHaveBeenCalledWith("索引任务连接中断");
  });

  test("非法 JSON 不抛错也不回调", () => {
    const { created, factory } = createFake();
    const onProgress = vi.fn();
    subscribeJobEvents("job-1", { onProgress }, factory);

    expect(() => created[0].emit("progress", "{not json")).not.toThrow();
    expect(onProgress).not.toHaveBeenCalled();
  });

  test("close() 幂等且阻止后续回调", () => {
    const { created, factory } = createFake();
    const onProgress = vi.fn();
    const sub = subscribeJobEvents("job-1", { onProgress }, factory);

    sub.close();
    sub.close();
    expect(sub.closed).toBe(true);
    expect(created[0].closed).toBe(true);
    created[0].emit("progress", { phase: "walk", state: "running" });
    expect(onProgress).not.toHaveBeenCalled();
  });
});

describe("normalizeProgress", () => {
  test("补全缺失数值字段", () => {
    expect(normalizeProgress({ phase: "resolve", state: "queued" })).toEqual({
      phase: "resolve",
      state: "queued",
      current: 0,
      total: 0,
      progress: 0,
      message: "",
    });
  });

  test("非对象或缺 phase/state 时返回 null", () => {
    expect(normalizeProgress(null)).toBeNull();
    expect(normalizeProgress("nope")).toBeNull();
    expect(normalizeProgress({ state: "running" })).toBeNull();
  });
});
