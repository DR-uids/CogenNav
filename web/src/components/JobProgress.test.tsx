import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, test } from "vitest";

import { useUi } from "../stores/ui";
import { JobProgress, phaseLabel, stateLabel } from "./JobProgress";

afterEach(() => {
  // 先卸载再重置 store，避免对已卸载组件之外的订阅者触发 React 更新告警
  cleanup();
  useUi.setState({ jobId: null, jobProgress: null });
});

describe("JobProgress", () => {
  test("按 current/total 渲染百分比与阶段中文标签", () => {
    useUi.setState({
      jobId: "job-1",
      jobProgress: {
        phase: "walk",
        state: "running",
        current: 3,
        total: 4,
        progress: 0.75,
        message: "遍历文件",
        file: "src/index.ts",
      },
    });

    render(<JobProgress />);

    expect(screen.getByTestId("job-progress-phase").textContent).toBe("Walking files");
    expect(screen.getByTestId("job-progress-percent").textContent).toBe("75%");
    expect(screen.getByTestId("job-progress-count").textContent).toBe("3/4");
    // 优先展示当前文件
    expect(screen.getByTestId("job-progress-message").textContent).toContain("src/index.ts");
    expect(screen.getByTestId("job-progress").getAttribute("data-state")).toBe("running");
  });

  test("没有 total 时退化为 progress 比率", () => {
    useUi.setState({
      jobId: "job-1",
      jobProgress: {
        phase: "clone",
        state: "running",
        current: 0,
        total: 0,
        progress: 0.42,
        message: "克隆仓库",
      },
    });

    render(<JobProgress />);

    expect(screen.getByTestId("job-progress-phase").textContent).toBe("Cloning repository");
    expect(screen.getByTestId("job-progress-percent").textContent).toBe("42%");
  });

  test("done 态即使计数未补齐也显示 100%", () => {
    useUi.setState({
      jobId: "job-1",
      jobProgress: {
        phase: "done",
        state: "done",
        current: 3,
        total: 4,
        progress: 1,
        message: "索引完成",
      },
    });

    render(<JobProgress />);

    expect(screen.getByTestId("job-progress-phase").textContent).toBe("Done");
    expect(screen.getByTestId("job-progress-percent").textContent).toBe("100%");
  });

  test("error 态展示红色错误信息", () => {
    useUi.setState({
      jobId: "job-1",
      jobProgress: {
        phase: "clone",
        state: "error",
        current: 1,
        total: 2,
        progress: 0.5,
        message: "clone 失败：仓库不存在",
      },
    });

    render(<JobProgress />);

    expect(screen.getByTestId("job-progress").getAttribute("data-state")).toBe("error");
    const message = screen.getByTestId("job-progress-message");
    expect(message.textContent).toContain("clone 失败");
    expect(message.className).toContain("text-rose-300");
    expect(screen.getByTestId("job-progress-percent").textContent).toBe("50%");
  });

  test("只有 jobId 没有进度时展示等待态", () => {
    useUi.setState({ jobId: "job-1", jobProgress: null });
    render(<JobProgress />);
    expect(screen.getByTestId("job-progress-phase").textContent).toContain("Waiting");
    expect(screen.getByTestId("job-progress-percent").textContent).toBe("0%");
  });

  test("没有 jobId 时不渲染任何内容", () => {
    useUi.setState({ jobId: null, jobProgress: null });
    render(<JobProgress />);
    expect(screen.queryByTestId("job-progress")).toBeNull();
  });

  test("阶段/状态标签映射与未知值兜底", () => {
    expect(phaseLabel("resolve")).toBe("Resolving target");
    expect(phaseLabel("done")).toBe("Done");
    expect(phaseLabel(null)).toBe("Idle");
    expect(phaseLabel("weird")).toBe("weird");
    expect(stateLabel("queued")).toBe("Queued");
    expect(stateLabel(undefined)).toBe("Idle");
  });
});
