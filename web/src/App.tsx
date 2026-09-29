import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import { subscribeJobEvents } from "./api/events";
import { JobProgress } from "./components/JobProgress";
import { Inspector } from "./components/Inspector";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { ViewTabs } from "./components/ViewTabs";
import { readDeepLinkParam, writeDeepLink } from "./lib/deepLink";
import { useUi, type ViewId } from "./stores/ui";
import { AskView } from "./views/AskView";
import { CstView } from "./views/CstView";
import { DirTreeView } from "./views/DirTreeView";
import { GraphView } from "./views/GraphView";
import { isViewId } from "./views/registry";

function renderView(view: ViewId) {
  switch (view) {
    case "tree":
      return <DirTreeView />;
    case "cst":
      return <CstView />;
    case "graph":
      return <GraphView />;
    case "ask":
      return <AskView />;
  }
}

export default function App() {
  const activeView = useUi((s) => s.activeView);
  const jobId = useUi((s) => s.jobId);
  const setJobProgress = useUi((s) => s.setJobProgress);
  const queryClient = useQueryClient();

  // 挂载时读一次深链 ?view=；之后视图切换由下面的 effect 回写（与 CstView 同一套做法）。
  useEffect(() => {
    const view = readDeepLinkParam("view");
    if (isViewId(view)) useUi.getState().setActiveView(view);
  }, []);

  const skipFirstViewWrite = useRef(true);
  useEffect(() => {
    if (skipFirstViewWrite.current) {
      skipFirstViewWrite.current = false;
      return;
    }
    writeDeepLink({ view: activeView });
  }, [activeView]);

  /**
   * SSE 订阅放在 App：任务跨视图存在，切换 Tab 不该重连。
   * 依赖只有 jobId，因此仅在提交新任务时重建连接；卸载/换任务时关闭。
   */
  useEffect(() => {
    if (!jobId) return;
    const current = () => useUi.getState().jobProgress;
    const done = (state: "done" | "error", message: string) => {
      const prev = current();
      setJobProgress({
        phase: state === "done" ? "done" : (prev?.phase ?? "walk"),
        state,
        current: prev?.current ?? 0,
        total: prev?.total ?? 0,
        progress: state === "done" ? 1 : (prev?.progress ?? 0),
        message,
        ...(prev?.file ? { file: prev.file } : {}),
      });
      // 终态后仓库摘要（fileCount/loc/languages）才有值，刷新列表。
      void queryClient.invalidateQueries({ queryKey: ["repos"] });
    };

    const subscription = subscribeJobEvents(jobId, {
      onProgress: (payload) => setJobProgress(payload),
      onDone: () => done("done", "索引完成"),
      onError: (message) => done("error", message),
    });

    return () => subscription.close();
  }, [jobId, setJobProgress, queryClient]);

  return (
    <div className="flex h-full flex-col">
      <TopBar />
      <div className="flex min-h-0 flex-1">
        <Sidebar />
        <main className="flex min-w-0 flex-1 flex-col">
          <JobProgress />
          <ViewTabs />
          <section className="min-h-0 flex-1 overflow-hidden" aria-label="视图内容">
            {renderView(activeView)}
          </section>
        </main>
        <Inspector />
      </div>
    </div>
  );
}
