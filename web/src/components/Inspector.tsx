import { useUi } from "../stores/ui";

/**
 * 右侧检查器：选中符号/文件后展示定义位置、代码片段与出入边。
 * M2 起接 CST 节点，M3 起接符号卡片。
 */
export function Inspector() {
  const selectedFile = useUi((s) => s.selectedFile);
  const selectedNode = useUi((s) => s.selectedNode);
  const hasSelection = Boolean(selectedFile || selectedNode);

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l border-zinc-800 bg-zinc-900/30">
      <div className="flex h-10 shrink-0 items-center border-b border-zinc-800 px-3">
        <h3 className="text-xs font-medium text-zinc-400">检查器</h3>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-3 text-xs">
        {hasSelection ? (
          <dl className="space-y-2">
            {selectedFile && (
              <div>
                <dt className="text-zinc-500">文件</dt>
                <dd className="font-mono break-all text-zinc-200">{selectedFile}</dd>
              </div>
            )}
            {selectedNode && (
              <div>
                <dt className="text-zinc-500">节点</dt>
                <dd className="font-mono break-all text-zinc-200">{selectedNode}</dd>
              </div>
            )}
          </dl>
        ) : (
          <div className="rounded border border-dashed border-zinc-800 p-3 leading-relaxed text-zinc-600">
            <p className="mb-2">未选中任何内容。</p>
            <p>选中文件或符号后，这里会显示：</p>
            <ul className="mt-2 list-disc space-y-1 pl-4">
              <li>定义位置与代码片段（M2）</li>
              <li>调用上下游与出入边（M3）</li>
              <li>影响面与待回归文件（M5）</li>
            </ul>
          </div>
        )}
      </div>
    </aside>
  );
}
