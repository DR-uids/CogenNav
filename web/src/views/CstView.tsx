import { useEffect, useRef } from "react";

import { CstFileList } from "../components/cst/CstFileList";
import { CstSource } from "../components/cst/CstSource";
import { CstTree } from "../components/cst/CstTree";
import { useUi } from "../stores/ui";

/** 深链参数：`?file=src/a.py&node=0.1`（只读，不引入路由库）。 */
function readDeepLink(): { file: string | null; node: string | null } {
  try {
    const params = new URLSearchParams(window.location.search);
    return { file: params.get("file"), node: params.get("node") };
  } catch {
    return { file: null, node: null };
  }
}

/**
 * CST 语法树视图（M2）：左侧文件列表 / 中间语法树 / 右侧源码，三栏联动。
 *
 * 选中态统一放在 `useUi`（selectedFile / selectedNode），三个面板各自按需取数：
 *  - 文件列表：GET /files（搜索防抖 + 虚拟滚动）
 *  - 语法树：GET /cst 根请求 depth=4，展开 truncated 节点时再按 nodePath 下钻
 *  - 源码：GET /file + shiki 高亮（失败回退纯文本）
 */
export function CstView() {
  const repoId = useUi((s) => s.repoId);
  const selectedFile = useUi((s) => s.selectedFile);
  const selectedNode = useUi((s) => s.selectedNode);

  // 挂载时读一次深链并写入 store；之后的选择变化由下面的 effect 回写 URL。
  useEffect(() => {
    const { file, node } = readDeepLink();
    const state = useUi.getState();
    if (file) state.selectFile(file);
    if (node) state.selectNode(node);
  }, []);

  // 切换仓库后，上一个仓库的文件/节点已无意义，清空以免请求 404。
  const prevRepoId = useRef(repoId);
  useEffect(() => {
    if (prevRepoId.current === repoId) return;
    const hadRepo = prevRepoId.current !== null;
    prevRepoId.current = repoId;
    if (!hadRepo) return;
    const state = useUi.getState();
    state.selectFile(null);
    state.selectNode(null);
  }, [repoId]);

  // 选择变化 → replaceState 同步 URL（保留其它参数如 ?view=，且不产生历史记录）。
  const skipFirstWrite = useRef(true);
  useEffect(() => {
    if (skipFirstWrite.current) {
      // 首次挂载的选中态来自深链，URL 本来就是对的，无需回写。
      skipFirstWrite.current = false;
      return;
    }
    const params = new URLSearchParams(window.location.search);
    if (selectedFile) params.set("file", selectedFile);
    else params.delete("file");
    // 根节点路径是空串，等价于「没有节点」，此时不写 node 参数。
    if (selectedNode) params.set("node", selectedNode);
    else params.delete("node");

    const query = params.toString();
    const url = `${window.location.pathname}${query ? `?${query}` : ""}${window.location.hash}`;
    window.history.replaceState(null, "", url);
  }, [selectedFile, selectedNode]);

  return (
    <div data-testid="view-cst" className="flex h-full min-h-0 bg-zinc-950">
      <CstFileList repoId={repoId} />
      {/* 用 file 作 key：换文件时重置展开状态与高亮，避免把上一个文件的节点路径带过去。 */}
      <CstTree key={`tree:${selectedFile ?? ""}`} repoId={repoId} file={selectedFile} />
      <CstSource key={`source:${selectedFile ?? ""}`} repoId={repoId} file={selectedFile} />
    </div>
  );
}
