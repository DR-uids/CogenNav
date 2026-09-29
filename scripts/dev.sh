#!/usr/bin/env bash
# 同时启动后端（热重载）与前端 dev server，Ctrl-C 一并退出。
set -euo pipefail

cd "$(dirname "$0")/.."

export PYTHONPATH="${PYTHONPATH:-$PWD/src}"
export COGEN_HOME="${COGEN_HOME:-$PWD/.cogen}"

PY="$PWD/.venv/bin/python"
PORT="${COGEN_PORT:-8765}"

if [ ! -x "$PY" ]; then
  echo "未找到虚拟环境，请先运行：make setup" >&2
  exit 1
fi

"$PY" -m uvicorn cogen.api.app:app --host 127.0.0.1 --port "$PORT" --reload &
API_PID=$!

cleanup() {
  kill "$API_PID" 2>/dev/null || true
  wait "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "后端: http://127.0.0.1:$PORT  (Ctrl-C 退出)"
echo "前端: http://127.0.0.1:5199"

cd web
pnpm dev
