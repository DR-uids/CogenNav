import { useQuery } from "@tanstack/react-query";

import { getHealth } from "../api/client";

function Dot({ tone }: { tone: "ok" | "warn" | "bad" }) {
  const color =
    tone === "ok" ? "bg-emerald-400" : tone === "warn" ? "bg-amber-400" : "bg-rose-500";
  return <span className={`inline-block size-2 rounded-full ${color}`} aria-hidden />;
}

/** 右上角后端健康状态：同时暴露 LLM 是否配置（决定 AI 功能是否降级）。 */
export function TopBar() {
  const { data, isError, isLoading } = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => getHealth(signal),
  });

  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-zinc-800 bg-zinc-900/60 px-4">
      <div className="flex items-baseline gap-3">
        <span className="font-semibold tracking-tight text-zinc-100">CogenNav</span>
        <span className="text-xs text-zinc-500">代码仓库 CST 解析 · 知识图谱导航</span>
      </div>

      <div className="flex items-center gap-3 text-xs">
        {isLoading && (
          <span className="flex items-center gap-1.5 text-zinc-400">
            <Dot tone="warn" /> 连接后端…
          </span>
        )}
        {isError && (
          <span className="flex items-center gap-1.5 text-rose-400" data-testid="health-error">
            <Dot tone="bad" /> 后端未启动（make dev）
          </span>
        )}
        {data && (
          <span
            className="flex items-center gap-1.5 text-zinc-400"
            data-testid="health-ok"
            title={data.home}
          >
            <Dot tone="ok" /> v{data.version}
            <span className="text-zinc-600">·</span>
            <span className={data.llm_configured ? "text-emerald-400" : "text-zinc-500"}>
              LLM {data.llm_configured ? "已配置" : "未配置"}
            </span>
          </span>
        )}
      </div>
    </header>
  );
}
