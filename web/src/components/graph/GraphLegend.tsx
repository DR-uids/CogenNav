import type { Community } from "../../api/client";
import { useT } from "../../i18n";
import { COMMUNITY_PALETTE, communityColor, hopColor, UNGROUPED_COLOR } from "../../lib/graphColors";

type GraphLegendProps = {
  communities: readonly Community[];
  selectedCommunity: number | null;
  onSelectCommunity: (id: number | null) => void;
  /** 影响面模式：把色阶解释成「第 N 跳」，社区色让位给跳数色。 */
  impactActive?: boolean;
};

/**
 * 图例：颜色 → 含义。力导向/分层模式下按社区着色，影响面模式下按跳数着色，
 * 两者共用同一份色板定义（lib/graphColors），因此图例与节点颜色永远一致。
 */
export function GraphLegend({
  communities,
  selectedCommunity,
  onSelectCommunity,
  impactActive = false,
}: GraphLegendProps) {
  const t = useT();

  return (
    <section
      data-testid="graph-legend"
      className="border-t border-zinc-800 px-3 py-2"
      aria-label={t("legend.aria")}
    >
      <h3 className="text-[10px] tracking-wide text-zinc-500 uppercase">
        {impactActive ? t("legend.hops") : t("legend.community")}
      </h3>

      {impactActive ? (
        <>
          <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
            {[1, 2, 3, 4, 5, 6].map((hop) => (
              <li
                key={hop}
                data-testid="legend-hop"
                data-hop={hop}
                className="flex items-center gap-1 text-[10px] text-zinc-400"
              >
                <span
                  aria-hidden="true"
                  className="h-2 w-2 rounded-full"
                  style={{ background: hopColor(hop) }}
                />
                {t("legend.hop", { hop })}
              </li>
            ))}
            <li className="flex items-center gap-1 text-[10px] text-zinc-400">
              <span
                aria-hidden="true"
                className="h-2 w-2 rounded-full"
                style={{ background: hopColor(0) }}
              />
              {t("legend.focus")}
            </li>
          </ul>
          <p className="mt-1 text-[10px] leading-relaxed text-zinc-600">
            {t("legend.impactHint")}
          </p>
        </>
      ) : (
        <>
          <ul className="mt-1 flex flex-col gap-1">
            {communities.length === 0 && (
              <li className="text-[10px] text-zinc-600">{t("legend.noCommunities")}</li>
            )}
            {communities.map((item) => {
              const active = item.id === selectedCommunity;
              return (
                <li key={item.id}>
                  <button
                    type="button"
                    data-testid="legend-community"
                    data-community={item.id}
                    data-active={active ? "true" : "false"}
                    aria-pressed={active}
                    onClick={() => onSelectCommunity(active ? null : item.id)}
                    title={t("legend.communityTitle", {
                      name: item.name,
                      size: item.size,
                      namedBy:
                        item.namedBy === "llm"
                          ? t("legend.namedByLlm")
                          : item.namedBy === "heuristic"
                            ? t("legend.namedByHeuristic")
                            : "",
                    })}
                    className={`flex w-full items-center gap-1.5 rounded px-1 py-0.5 text-left text-[10px] ${
                      active ? "bg-zinc-800 text-zinc-100" : "text-zinc-400 hover:bg-zinc-900"
                    }`}
                  >
                    <span
                      aria-hidden="true"
                      className="h-2 w-2 shrink-0 rounded-full"
                      style={{ background: communityColor(item.id) }}
                    />
                    <span className="truncate">{item.name}</span>
                    <span className="ml-auto shrink-0 text-zinc-600">{item.size}</span>
                  </button>
                </li>
              );
            })}
          </ul>
          <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-zinc-500">
            <li className="flex items-center gap-1">
              <span
                aria-hidden="true"
                className="h-2 w-2 rounded-full"
                style={{ background: UNGROUPED_COLOR }}
              />
              {t("legend.ungrouped")}
            </li>
            <li>{t("legend.sizeIsDegree")}</li>
            <li>
              {t("legend.strokeIs")}
              <span className="ml-1 font-mono text-zinc-400">kind</span>
            </li>
          </ul>
          <p className="mt-1 text-[10px] text-zinc-600">
            {t("legend.palette", { count: COMMUNITY_PALETTE.length })}
          </p>
        </>
      )}
    </section>
  );
}
