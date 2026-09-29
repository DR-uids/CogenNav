# CogenNav

把任意代码仓库（GitHub / 本地路径）解析成 **可下钻的 CST 语法树** 与 **可查询的符号知识图谱**，并在本地 Web 界面中导航：目录树 → 单文件语法树 → 跨文件调用/依赖图，附带 AI 社区命名、自然语言问答与 MCP 接入。

> 当前进度：**M0–M5 已完成**（骨架 / 摄取 / CST / 图谱 / AI / MCP 与导出），正在做 M6 加固交付。里程碑与验收标准见实施计划。
>
> 已实现（M1）：`POST /api/repos`（本地路径 / `owner/repo` / https / scp 形式，协议白名单 + 参数注入防护）、`GET /api/repos`、`GET|DELETE /api/repos/{id}`、`GET /api/jobs/{id}`、`GET /api/jobs/{id}/events`（SSE 进度，含终态回放）、浅克隆（体积/时长看门狗、token 走环境变量）、`.gitignore` 感知遍历（上限、二进制、密钥文件、符号链接跳过）。
>
> 已实现（M2）：`GET /api/repos/{id}/files`、`GET /api/repos/{id}/file`、`GET /api/repos/{id}/cst`（惰性子树 / `format=sexp` 降级 / 无语法返回 415）、分进程解析池（墙钟看门狗 + worker 环境变量裁剪 + 单进程降级）、解析统计回填（节点数 / 语法错误数）。
>
> 已实现（M3）：`GET /api/repos/{id}/tree|analysis|graph|graph/neighbors|graph/path|graph/impact|search|symbol`；抽取器覆盖 Python / TypeScript+TSX+JS / Go / Java（其余语言走通用启发式）；跨文件调用三档置信度（EXTRACTED / INFERRED / AMBIGUOUS）；Louvain 社区 + import 环 + god nodes + 孤儿文件；节点边与解析统计落 SQLite 并建 FTS5 索引。
>
> 实测（`psf/requests`，122 文件）：索引 2.5s → 1306 节点 / 1947 边 / 56 社区 / 366 条 calls 边，**调用解析率 67%**（无类型推断的静态分析上限附近），god nodes = `TestRequests`/`Response`/`Session`/`RequestsCookieJar`/`HTTPAdapter`；发现 1 个真实 import 环（11 个 `src/requests/*.py`）。fixture 仓库（`tests/fixtures/graph_repo`）解析率 ≥ 80% 作为验收基线。
>
> 已实现（M4/M5）：`/ask`（SSE：工具轨迹 + token 流 + 引用节点）、`/ai/status`、`/communities/name`（LLM 命名 + 内容指纹缓存 + 确定性降级）、`/summary`；`cogen export`（graph.json / GRAPH_REPORT.md / 单文件 graph.html）；`cogen mcp`（stdio 与 streamable-http，13 个只读工具）。
>
> 实测（M2，同上仓库）：对 30707 节点的 `tests/test_requests.py` 取 `depth=4` 根切片 = HTTP **35ms**、gzip 后 **31KB**（整树约 3MB），懒加载单个子树 0.5ms。

## 已知坑（务必遵守）

- **不要访问 `node.start_point` / `end_point` / `range`**。py-tree-sitter 0.26.0 返回的 `Point` 原生对象在数千节点规模上会破坏堆，随后在 GC 或解释器退出时 **Bus error / Segmentation fault**（本项目实测稳定复现）。行列一律用 `cogen.parse.tscompat.SourceIndex` 从 `start_byte/end_byte` 换算，并有子进程回归测试守护（`tests/test_parse.py::test_cst_serialization_does_not_crash_interpreter`）。
- **分进程解析池用 `spawn`**：父进程若是没有 `if __name__ == "__main__"` 保护的交互式脚本，worker 会启动失败；此时 `ParsePool` 会自动完成健康检查并**降级为单进程解析**（`meta.parse.mode == "serial"`），功能可用但没有超时保护。
- **Node 包装对象不能用 `is` / `==` 比较**：每次属性访问都会新建 Python 包装器（实测 `is` 恒为 False），判断是否同一节点只能用 `tscompat.same_node(a, b)`（内部比原生 `id`）。
- **tree-sitter 的字段名常与节点类型名不一致**：Java `implements` 的节点类型是 `super_interfaces`，但字段名是 `interfaces`；`field_declaration` 的 `modifiers` 取不到字段、只能扫子节点；Go 分组导入多一层 `import_spec_list`，导入必须用 `ctx.walk()` 而不是 `named_children`。写新语言抽取器时先打印 `field_name_for_child` 再下手。
- **多名字声明只返回最后一个名字**：Go `const A, B = 1, 2` 的 `name` 字段只有 `B`，其余要扫兄弟节点。
- **截断的源码可能整段变成 `ERROR` 节点**：Java `class A { void f(` 连 `class_declaration` 都不存在，抽取器必须把"抽不到"当正常情况处理。

## 快速开始

```bash
make setup     # 创建 .venv 并安装后端 + 前端依赖
make dev       # 后端 http://127.0.0.1:8765 ，前端 http://127.0.0.1:5199
```

`make dev` 同时启动 FastAPI（8765，热重载）与 Vite dev server（5199，`/api` 代理到后端）。
生产形态：`make build-web` 后 `cogen serve`，由 FastAPI 直接托管 `web/dist`。

其他常用命令：

```bash
make test      # 后端测试（默认跳过 network 标记）
make test-all  # 后端 + 前端测试
make lint      # ruff + mypy + tsc
make fmt       # 自动格式化
cogen status   # 查看配置、数据目录、已索引仓库
cogen index ./some/repo     # 不开服务，直接索引并打印摘要（--json 可脚本化）
cogen index psf/requests --ref v2.31.0
cogen export --repo <repoId>  # 导出三种产物；--format json|md|html 只导一种
cogen mcp --repo <repoId>     # 以 stdio 启动 MCP Server（给 AI 编程助手用）
cogen mcp --http --port 8770  # 以 Streamable HTTP 启动（团队共享）
cogen warm rust             # 仅安装了 cogen[xlang] 时需要：预取扩展语言语法
make demo                   # 一键：索引 fixture → 导出三种产物 → 打印入口
PYTHONPATH=src .venv/bin/python scripts/bench.py psf/requests   # 解析/CST 性能基准
```

## 接入 AI 编程助手（MCP）

`cogen mcp` 暴露 **13 个只读工具**（`repo_overview` / `search_symbols` / `get_symbol` /
`neighbors` / `callers` / `callees` / `path_between` / `impact` / `file_tree` / `read_file` /
`get_cst` / `list_communities` / `module_dependencies`）与 3 个资源
（`cogen://repo/overview` / `cogen://repo/analysis` / `cogen://repo/tree`）。
**没有任何执行或写文件的入口**——助手只能查图谱，不能跑你的代码。

Claude Code / Cursor 等支持 MCP 的客户端里加一段配置（把路径换成你的仓库）：

```json
{
  "mcpServers": {
    "cogen": {
      "command": "/Users/you/Code/CogenNav/.venv/bin/cogen",
      "args": ["mcp", "--repo", "github.com__psf__requests@head-116d73"],
      "env": { "COGEN_HOME": "/Users/you/Code/CogenNav/.cogen" }
    }
  }
}
```

调试用官方 Inspector：`npx @modelcontextprotocol/inspector cogen mcp --repo <repoId>`。
省略 `--repo` 时使用最近索引的那个仓库；没索引过会明确提示先 `cogen index <target>`。

需要真实克隆的测试（默认跳过）：

```bash
pytest -q -m network                  # 真实浅克隆 octocat/Hello-World
pytest -q -m "not network"            # 纯离线套件（CI 默认）
```

## 技术栈

| 层 | 选型 |
|---|---|
| CST 解析 | tree-sitter 0.26 + 核心语言 wheel（约 19 门，离线可用）；371 语言扩展包默认**关闭**（`pip install -e ".[xlang]"` 显式启用） |
| 抽取与图分析 | 自研 per-language extractor + NetworkX（Louvain 社区、环检测、PageRank） |
| 存储 | SQLite（图谱 + FTS5 检索 + 任务状态），一仓一库 |
| 服务 | FastAPI + SSE（索引进度流式推送），仅监听 127.0.0.1 |
| 前端 | React 19 + Vite + TypeScript + Tailwind 4；可视化用 Sigma(WebGL) 大图 + React Flow/Dagre 分层 DAG |
| AI | OpenAI 兼容客户端（社区命名 + 架构摘要 + 图谱 tool-calling 问答） |
| 助手集成 | MCP Server（stdio / Streamable HTTP），暴露与问答**相同**的只读工具集 |

## 设计要点

- **不执行被测代码**：解析只做 `open(bytes)` + tree-sitter；唯一子进程是 `git`。不跑仓库的构建、测试或 hook。
- **解析进程隔离**：分进程池 + 每块墙钟超时熔断（`py-tree-sitter` 本身没有 timeout API）+ worker 环境变量白名单（剥离 LLM Key 与 git token）。
- **单一事实源**：Web API、自然语言问答、MCP 全部复用 `cogen.graph.tools` 的只读查询函数。
- **降级优先**：无 LLM Key → 确定性社区命名；无专用 extractor → 通用启发式；语法缺失 → 仅登记文件。
- **可寻址**：前端 URL 承载状态（`/r/{repoId}?view=cst&file=...&node=0.1.2&line=12`），任意视图可分享、可前进后退。

## 沙箱与缓存目录（重要）

本项目的开发环境限定**只能写工作区内**的路径（工作区外写入会被拒绝）。因此所有缓存都落在仓库目录中，`Makefile` 已自动设置：

| 变量 | 值 | 原因 |
|---|---|---|
| `PIP_CACHE_DIR` | `.cache/pip` | `~/Library/Caches` 在工作区外 |
| `TMPDIR` | `.cache/tmp` | 同上 |
| `COGEN_HOME` | `.cogen` | 仓库快照 / SQLite / 导出产物 |
| `TREE_SITTER_LANGUAGE_PACK_CACHE_DIR` | `.cogen/cache/grammars` | 扩展语言包的语法下载缓存 |
| `PNPM_HOME` + `--store-dir .pnpm-store` | 工作区内 | 本机 npm 缓存已损坏（EPERM），统一用 pnpm |

## 配置

全部配置项均可用 `COGEN_` 前缀的环境变量覆盖，详见 [.env.example](.env.example)：规模上限、克隆超时、`GITHUB_TOKEN`、LLM 端点与脱敏开关。

架构总览、数据契约、接口清单与安全基线见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 目录结构

```
src/cogen/
  cli.py config.py           # 入口与配置
  ingest/                    # target 解析、浅克隆（git 硬化）、文件遍历
  parse/                     # 语言注册表、CST 导出、分进程解析池
  extract/                   # per-language extractor + 通用启发式 + 调用解析
  graph/                     # schema / SQLite store / build / analyze / tools
  ai/                        # LLM 客户端、社区命名、问答
  mcp/                       # MCP Server
  api/                       # FastAPI 路由（repos/jobs/tree/cst/graph/ai）
  export/                    # graph.json / GRAPH_REPORT.md / graph.html
web/                         # React 单页应用
tests/                       # 单测 + 接口测试 + fixture 仓库
```

## 里程碑与验收现状

| 里程碑 | 内容 | 状态 | 关键验收证据 |
|---|---|---|---|
| M0 | 骨架与开发闭环 | ✅ | `make setup/dev/lint/test` 全通；缓存全部落在工作区内 |
| M1 | 摄取与 git 硬化 | ✅ | 真实浅克隆 `octocat/Hello-World`；`ext::`/`file://`/`--upload-pack` 全部拒绝；SSE 实时与终态回放 |
| M2 | CST 解析与视图 | ✅ | 19 门语言；30707 节点文件 `/cst` 根切片 35ms / gzip 31KB；病态输入超时熔断；worker 环境裁剪 |
| M3 | 抽取与知识图谱 | ✅ | 三档置信度；fixture 解析率 ≥80%；无悬空边/重复 id；requests：1306 节点 / 56 社区 / 67% 解析率 |
| M4 | AI 层 | ✅ | 社区命名（LLM + 内容指纹缓存 + 确定性降级）；`/ask` 工具轨迹 + token 流 + 引用；未配置 Key 时明确降级 |
| M5 | MCP / 导出 / 影响面 | ✅ | 13 个只读工具（stdio 子进程实测可调用）；`cogen export --format json\|md\|html`；增量重索引只解析变更文件（requests：2.37s → 0.12s，改 1 文件 0.39s，`parse.parsed == 1` 有测试断言） |
| M6 | 加固与交付 | 🔄 | 边界测试（编码/CRLF/超长行/极深路径/Unicode/空仓库/子模块/断链）；覆盖率门槛 70%（实测 84%）；`make demo` 一键跑通 |

## 测试与质量门禁

```bash
make test        # 后端 232 个测试（默认跳过 network）
make test-all    # 后端 + 前端（113 个测试）
make coverage    # 后端覆盖率（门槛 70%，实测 ~84%）
make lint        # ruff check + ruff format --check + mypy + tsc
make demo        # 干净环境跑通：索引 fixture → 三种产物
pytest -q -m network   # 需要真实克隆的用例（默认不跑）
```

覆盖率说明：解析/抽取跑在 `spawn` 出来的 worker 进程里，父进程的覆盖率统计不到它们，
因此 `tests/test_extract_core.py` 与 `tests/test_extract_langs.py` 会**在进程内直连**调用
各语言 extractor，专门守住这部分逻辑。

## 已知限制（诚实清单）

- **调用解析率不是 100%**：没有类型推断，`obj.method()`（`obj` 是局部变量或参数）一律不猜，
  计入 `unresolved`。requests 实测 67%，fixture（纯可解析调用）≥80%。想进一步提高需要局部类型推断。
- **只有三档置信度，没有跨语言调用**：Python↔JS 这类跨语言调用不会连边（同一个仓库里极少见）。
- **不持久化语法树**：CST 视图按需重新解析（有 8 个文件的 LRU 缓存），百万行仓库会重复付出解析成本。
- **扩展语言（371 门）默认关闭**：需要 `pip install -e ".[xlang]"`，且首次解析会联网下载**原生**解析器。
- **增量索引只省"解析+抽取"**：按文件 sha256 + 抽取结果缓存跳过未变更文件（requests 二次索引 2.37s → **0.12s**，改 1 个文件 0.39s），但建图/社区分析仍全量重跑（这一步是纯内存计算，几十毫秒级）。
- **没有浏览器端截图回归**：前端以 vitest + 构建为门禁，视觉验证需要人工打开页面。
