import { useT } from "../i18n";
import { viewMeta, type ViewMeta } from "../views/registry";

/** 未实现视图的统一占位：明确写出归属里程碑与将要交付的能力。 */
export function ComingSoon({ meta }: { meta: ViewMeta }) {
  const t = useT();

  return (
    <div
      className="flex h-full items-center justify-center p-8"
      data-testid={`view-${meta.id}`}
    >
      <div className="max-w-lg rounded-lg border border-zinc-800 bg-zinc-900/40 p-6">
        <div className="mb-2 flex items-center gap-2">
          <span className="rounded bg-zinc-800 px-2 py-0.5 font-mono text-xs text-amber-300">
            {meta.milestone}
          </span>
          <h2 className="text-lg font-medium text-zinc-100">{t(meta.labelKey)}</h2>
        </div>
        <p className="text-sm leading-relaxed text-zinc-400">{t(meta.blurbKey)}</p>
        <p className="mt-4 text-xs text-zinc-600">
          {t("comingSoon.delivered", { milestone: meta.milestone })}
        </p>
      </div>
    </div>
  );
}

export function Placeholder({ id }: { id: ViewMeta["id"] }) {
  return <ComingSoon meta={viewMeta(id)} />;
}
