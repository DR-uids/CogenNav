import { create } from "zustand";

import type { JobProgressPayload } from "../api/events";

/** 视图标识，同时作为 URL 的 `?view=` 取值（M2/M3 起支持深链）。 */
export type ViewId = "tree" | "cst" | "graph" | "ask";

/** 当前索引进度（SSE 归一化后的结构），无任务时为 null。 */
export type JobProgressInfo = JobProgressPayload;

/**
 * `selectedNode` 的来源：CST 视图存的是 nodePath（`"0.1.2"`），
 * 图谱/引用存的是符号 id（`"python:src/a.py#f.function"`）。两套命名空间不通用，
 * 靠这个字段区分，图谱视图才敢响应 store 里选中变化（见 GraphView 的 effect）。
 */
export type SelectionOrigin = "cst" | "graph";

export type UiState = {
  activeView: ViewId;
  repoId: string | null;
  /** 正在跟踪的索引任务 id：App 依据它建立/重建 SSE 订阅。 */
  jobId: string | null;
  jobProgress: JobProgressInfo | null;
  selectedFile: string | null;
  selectedNode: string | null;
  /** selectedNode 的命名空间；没有选中时为 null。 */
  selectionOrigin: SelectionOrigin | null;
  /** 每次 selectNode 自增：同一个 id 连续选两次也要能再次触发订阅方。 */
  selectionNonce: number;
  /** 调用顺序表示：视图切换后也应保持已选文件/节点，便于联动下钻。 */
  setActiveView: (view: ViewId) => void;
  setRepoId: (repoId: string | null) => void;
  /** 清空 jobId 时一并清掉进度，避免残留上一个任务的进度条。 */
  setJobId: (jobId: string | null) => void;
  setJobProgress: (progress: JobProgressInfo | null) => void;
  selectFile: (file: string | null) => void;
  /** origin 默认 cst：既有调用点（CST/符号卡）不用改，语义也不变。 */
  selectNode: (nodeId: string | null, origin?: SelectionOrigin) => void;
};

export const useUi = create<UiState>((set) => ({
  activeView: "tree",
  repoId: null,
  jobId: null,
  jobProgress: null,
  selectedFile: null,
  selectedNode: null,
  selectionOrigin: null,
  selectionNonce: 0,
  setActiveView: (activeView) => set({ activeView }),
  setRepoId: (repoId) => set({ repoId }),
  setJobId: (jobId) => set(jobId ? { jobId } : { jobId: null, jobProgress: null }),
  setJobProgress: (jobProgress) => set({ jobProgress }),
  selectFile: (selectedFile) => set({ selectedFile }),
  selectNode: (selectedNode, origin = "cst") =>
    set((state) => ({
      selectedNode,
      selectionOrigin: selectedNode ? origin : null,
      selectionNonce: state.selectionNonce + 1,
    })),
}));
