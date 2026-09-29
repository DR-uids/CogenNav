import { useQuery } from "@tanstack/react-query";

import { getHealth } from "../api/client";
import { useT } from "../i18n";
import { LocaleSwitch } from "./LocaleSwitch";

function Dot({ tone }: { tone: "ok" | "warn" | "bad" }) {
  const color =
    tone === "ok" ? "bg-emerald-400" : tone === "warn" ? "bg-amber-400" : "bg-rose-500";
  return <span className={`inline-block size-2 rounded-full ${color}`} aria-hidden />;
}

/** 右上角后端健康状态：同时暴露 LLM 是否配置（决定 AI 功能是否降级）。 */
export function TopBar() {
  const t = useT();
  const { data, isError, isLoading } = useQuery({
    queryKey: ["health"],
    queryFn: ({ signal }) => getHealth(signal),
  });

  return (
    <header className="flex h-12 shrink-0 items-center justify-between border-b border-zinc-800 bg-zinc-900/60 px-4">
      <div className="flex items-baseline gap-3">
        <span className="font-semibold tracking-tight text-zinc-100">CogenNav</span>
        <span className="text-xs text-zinc-500">{t("topbar.tagline")}</span>
      </div>

      <div className="flex items-center gap-3 text-xs">
        {isLoading && (
          <span className="flex items-center gap-1.5 text-zinc-400">
            <Dot tone="warn" /> {t("topbar.connecting")}
          </span>
        )}
        {isError && (
          <span className="flex items-center gap-1.5 text-rose-400" data-testid="health-error">
            <Dot tone="bad" /> {t("topbar.offline")}
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
              {data.llm_configured ? t("topbar.llmConfigured") : t("topbar.llmUnconfigured")}
            </span>
          </span>
        )}
        <LocaleSwitch />
      </div>
    </header>
  );
}
