#!/usr/bin/env bash
# 一键演示：索引 fixture 仓库 → 导出三种产物 → 打印如何查看。
# 用法：make demo  （或 bash scripts/demo.sh <target>）
set -euo pipefail

cd "$(dirname "$0")/.."

PY="$PWD/.venv/bin/python"
export PYTHONPATH="${PYTHONPATH:-$PWD/src}"
export COGEN_HOME="${COGEN_HOME:-$PWD/.cogen/demo}"
export TMPDIR="${TMPDIR:-$PWD/.cache/tmp}"
TARGET="${1:-tests/fixtures/graph_repo}"

if [ ! -x "${PY}" ]; then
  echo "未找到虚拟环境，请先运行：make setup" >&2
  exit 1
fi

mkdir -p "${COGEN_HOME}" "${TMPDIR}"

echo "== 1/3 索引 ${TARGET} (COGEN_HOME=${COGEN_HOME})"
INDEX_JSON="$("${PY}" -m cogen.cli index "${TARGET}" --json)"
REPO_ID="$(printf '%s' "${INDEX_JSON}" | "${PY}" -c 'import json,sys; print(json.load(sys.stdin)["repoId"])')"
printf '   repoId = %s\n' "${REPO_ID}"

echo "== 2/3 导出 graph.json / GRAPH_REPORT.md / graph.html"
EXPORT_JSON="$("${PY}" -m cogen.cli export --repo "${REPO_ID}" --json)"
printf '%s' "${EXPORT_JSON}" | "${PY}" -c '
import json, os, sys
info = json.load(sys.stdin)
for label, key in (("graph.json", "graph"), ("GRAPH_REPORT.md", "report"), ("graph.html", "html")):
    path = info.get(key)
    if path:
        print(f"   {label:16s} {os.path.getsize(path) / 1024:8.1f} KiB  {path}")
'

echo "== 3/3 结果摘要"
printf '%s' "${INDEX_JSON}" | "${PY}" -c '
import json, sys
info = json.load(sys.stdin)
parse = info.get("parse") or {}
print("   文件 {} 个 / {} 行; 解析 {} 个 ({} 个 CST 节点)".format(
    info["fileCount"], info["loc"], parse.get("parsed", 0), parse.get("nodes", 0)))
print("   语言: {}".format(info["languages"]))
'
printf '%s' "${EXPORT_JSON}" | "${PY}" -c '
import json, os, sys
print("   产物目录: " + os.path.dirname(json.load(sys.stdin)["graph"]))
'

cat <<'TIP'

下一步（可选）：
  make dev                       # 起服务，浏览器打开 http://127.0.0.1:5199 看三视图
  open <上面 graph.html 的路径>    # 离线单文件图谱（无需服务）
  cogen mcp --repo <repoId>      # 给 Claude Code / Cursor 接入（stdio）
TIP
