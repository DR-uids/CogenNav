import type { Community } from "../../api/client";
import { useT } from "../../i18n";
import { isChecked } from "../../lib/graphFilters";
import { communityColor } from "../../lib/graphColors";

type OptionGroupProps = {
  title: string;
  testId: string;
  options: readonly string[];
  selected: readonly string[];
  onToggle: (value: string) => void;
  /** 该选项是否被强制禁用（隐藏 AMBIGUOUS 时的 ambiguous）。 */
  disabledValue?: string | undefined;
  hint?: string;
};

/** 一组复选：空 selected 表示「隐式全选」。 */
function OptionGroup({
  title,
  testId,
  options,
  selected,
  onToggle,
  disabledValue,
  hint,
}: OptionGroupProps) {
  const t = useT();

  return (
    <fieldset className="border-t border-zinc-800 px-3 py-2">
      <legend className="text-[10px] tracking-wide text-zinc-500 uppercase">{title}</legend>
      <div className="mt-1 flex flex-col gap-1">
        {options.length === 0 && (
          <span className="text-[10px] text-zinc-600">{t("filter.noData")}</span>
        )}
        {options.map((option) => {
          const checked = isChecked(selected, option);
          const disabled = option === disabledValue;
          return (
            <label
              key={option}
              className={`flex items-center gap-1.5 text-[11px] ${
                disabled ? "text-zinc-600" : "text-zinc-300"
              }`}
            >
              <input
                type="checkbox"
                data-testid={testId}
                data-value={option}
                checked={checked && !disabled}
                disabled={disabled}
                onChange={() => onToggle(option)}
                className="h-3 w-3 accent-sky-500"
              />
              <span className="truncate font-mono">{option}</span>
            </label>
          );
        })}
      </div>
      {hint && <p className="mt-1 text-[10px] leading-relaxed text-zinc-600">{hint}</p>}
    </fieldset>
  );
}

type GraphFiltersProps = {
  availableKinds: readonly string[];
  availableRelations: readonly string[];
  kinds: readonly string[];
  relations: readonly string[];
  confidence: readonly string[];
  hideAmbiguous: boolean;
  onToggleKind: (kind: string) => void;
  onToggleRelation: (relation: string) => void;
  onToggleConfidence: (confidence: string) => void;
  onHideAmbiguousChange: (value: boolean) => void;
  communities: readonly Community[];
  community: number | null;
  onCommunityChange: (id: number | null) => void;
  onReset: () => void;
  /** 当前图规模（节点/边），让「过滤有没有生效」一眼可见。 */
  nodeCount: number;
  edgeCount: number;
  /** 当前模式的能力提示（例如分层 DAG 只支持关系过滤）。 */
  notice?: string | undefined;
};

/**
 * 过滤面板：节点类型 / 关系 / 置信度（含「隐藏 AMBIGUOUS」）/ 社区。
 * 任一改动都会换 queryKey → 重新请求（后端做过滤，前端不自己剪边，避免与 truncated 语义打架）。
 */
export function GraphFilters({
  availableKinds,
  availableRelations,
  kinds,
  relations,
  confidence,
  hideAmbiguous,
  onToggleKind,
  onToggleRelation,
  onToggleConfidence,
  onHideAmbiguousChange,
  communities,
  community,
  onCommunityChange,
  onReset,
  nodeCount,
  edgeCount,
  notice,
}: GraphFiltersProps) {
  const t = useT();

  return (
    <section
      data-testid="graph-filters"
      className="flex w-60 shrink-0 flex-col overflow-auto border-r border-zinc-800 bg-zinc-900/20"
    >
      <div className="flex h-10 shrink-0 items-center justify-between px-3">
        <h3 className="text-xs font-medium text-zinc-400">{t("filter.title")}</h3>
        <button
          type="button"
          data-testid="filter-reset"
          onClick={onReset}
          className="rounded border border-zinc-800 px-1.5 py-0.5 text-[10px] text-zinc-400 hover:bg-zinc-900 hover:text-zinc-200"
        >
          {t("filter.reset")}
        </button>
      </div>

      {notice && (
        <p
          data-testid="filter-notice"
          className="border-b border-zinc-800 px-3 py-1.5 text-[10px] leading-relaxed text-amber-400/90"
        >
          {notice}
        </p>
      )}

      <div className="px-3 pb-2 text-[10px] text-zinc-600">
        <span data-testid="graph-count">{t("filter.nodes", { count: nodeCount })}</span>
        <span className="mx-1">/</span>
        <span data-testid="graph-edge-count">{t("filter.edges", { count: edgeCount })}</span>
      </div>

      <OptionGroup
        title={t("filter.kind")}
        testId="filter-kind"
        options={availableKinds}
        selected={kinds}
        onToggle={onToggleKind}
        hint={t("filter.kindHint")}
      />

      <OptionGroup
        title={t("filter.relation")}
        testId="filter-relation"
        options={availableRelations}
        selected={relations}
        onToggle={onToggleRelation}
        hint={t("filter.relationHint")}
      />

      <OptionGroup
        title={t("filter.confidence")}
        testId="filter-confidence"
        options={["extracted", "inferred", "ambiguous"]}
        selected={confidence}
        onToggle={onToggleConfidence}
        disabledValue={hideAmbiguous ? "ambiguous" : undefined}
      />

      <fieldset className="border-t border-zinc-800 px-3 py-2">
        <label className="flex items-center gap-1.5 text-[11px] text-zinc-300">
          <input
            type="checkbox"
            data-testid="filter-hide-ambiguous"
            checked={hideAmbiguous}
            onChange={(event) => onHideAmbiguousChange(event.target.checked)}
            className="h-3 w-3 accent-sky-500"
          />
          {t("filter.hideAmbiguous")}
        </label>
        <p className="mt-1 text-[10px] leading-relaxed text-zinc-600">
          {t("filter.hideAmbiguousHint")}
        </p>
      </fieldset>

      <fieldset className="border-t border-zinc-800 px-3 py-2">
        <legend className="text-[10px] tracking-wide text-zinc-500 uppercase">
          {t("filter.community")}
        </legend>
        <select
          data-testid="filter-community"
          value={community === null ? "" : String(community)}
          onChange={(event) =>
            onCommunityChange(event.target.value === "" ? null : Number(event.target.value))
          }
          className="mt-1 w-full rounded border border-zinc-800 bg-zinc-950 px-1.5 py-1 text-[11px] text-zinc-200 focus:border-zinc-600 focus:outline-none"
        >
          <option value="">{t("filter.allCommunities")}</option>
          {communities.map((item) => (
            <option key={item.id} value={String(item.id)}>
              {t("filter.communityOption", { name: item.name, size: item.size })}
            </option>
          ))}
        </select>
        {community !== null && (
          <span
            className="mt-1 inline-flex items-center gap-1 text-[10px] text-zinc-400"
            data-testid="filter-community-active"
          >
            <span
              aria-hidden="true"
              className="h-2 w-2 rounded-full"
              style={{ background: communityColor(community) }}
            />
            {t("filter.communityActive")}
          </span>
        )}
      </fieldset>
    </section>
  );
}
