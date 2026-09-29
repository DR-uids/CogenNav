# CogenNav 架构说明

本文档描述系统的分层、数据契约与安全边界。里程碑与验收标准见 README 的「里程碑与验收现状」。

## 1. 管线

```
target(local | git) → resolve → clone → walk → parse+extract → resolve_calls → build → analyze → (LLM naming)
                     ingest/    ingest/ ingest/ parse+extract  extract/       graph/   graph/     ai/
```

| 阶段 | 模块 | 输入 → 输出 | 为什么这么切 |
|---|---|---|---|
| resolve | `ingest/resolve.py` | 目标字符串 → `Target` + repoId | 不联网、只做形态识别与安全校验，repoId 决定后续所有路径 |
| clone | `ingest/clone.py` | `Target` → 快照目录 + commit | 隔离 git 环境（`GIT_CONFIG_GLOBAL=/dev/null`、协议白名单），体积/时长双看门狗 |
| walk | `ingest/walk.py` | 快照目录 → `FileRecord[]` + 跳过清单 | `.gitignore` 语义、上限保护、二进制/密钥/生成物跳过，不跟随符号链接 |
| parse+extract | `parse/pool.py` + `extract/*` | 文件 → `FileAnalysis(outcome, extraction)` | 解析与抽取同进程完成，避免把语法树搬回主进程；分进程带看门狗 |
| resolve_calls | `extract/resolve_calls.py` | 全部抽取结果 → 引用边 + 置信度 | 只有看到整仓才能判断"谁调用了谁"，必须在主进程做 |
| build | `graph/build.py` | 文件 + 抽取 + 引用 → 节点/边 | 纯函数，便于契约测试（无悬空边、无重复 id） |
| analyze | `graph/analyze.py` | 节点边 → 社区/环/中心性 | NetworkX Louvain + 强连通分量，固定随机种子保证可复现 |
| naming | `ai/naming.py` | 社区 → 人类可读名 | LLM 可选；未配置时确定性启发式，结果按内容指纹缓存 |

阶段之间只通过 plain dict / dataclass / SQLite 表通信，任一阶段可单独测试。

## 2. 模块地图

```
src/cogen/
  config.py            全量配置（COGEN_ 前缀环境变量）
  security.py          目标/路径校验 + 密钥脱敏（唯一入口）
  jobs.py              任务管理器、SSE 进度广播、命名缓存无关的进度节流
  ingest/              resolve / clone / walk / pipeline
  parse/               languages(注册表) / tscompat(API 适配) / parser / cst(惰性切片) / pool(看门狗)
  extract/             base(契约) / python / typescript / go / java / generic / resolve_calls / registry
  graph/               schema / store(SQLite) / build / analyze / tools(只读查询)
  ai/                  llm(客户端) / naming(命名与摘要) / ask(工具调用问答)
  mcp/server.py        MCP Server（stdio / streamable-http）
  api/                 app + routes_{repos,jobs,files,graph,ai} + deps
  export/              json_export / report / html_export
web/                   React 单页应用（目录树 / CST / 图谱 / 问答）
scripts/               dev.sh（并行起服务）、demo.sh（一键演示）、bench.py（性能基准）
tests/                 单测 + 接口测试 + fixtures/graph_repo（多语言图谱 fixture）
```

## 3. 数据契约

### 3.1 CST（`/api/repos/{id}/cst`）

```json
{ "path": "src/a.py", "language": "python", "nodePath": "0.1", "depth": 4, "totalNodes": 1225,
  "stale": false,
  "node": { "type": "function_definition", "named": true, "field": null,
            "start": [12, 0], "end": [14, 20], "startByte": 245, "endByte": 300,
            "childCount": 5, "error": false, "missing": false,
            "truncated": false, "childrenCapped": false,
            "text": null, "children": [ /* 同构 */ ] } }
```

- `nodePath`：根为 `""`，第 i 个子节点为 `P === "" ? String(i) : `${P}.${i}``；索引**全部**子节点（含匿名）。
- `truncated`：因 `depth` 未返回子节点（前端据此懒加载）；`childrenCapped`：子节点数超过上限被截（**不可**再懒加载）。
- 行列来自 `parse/tscompat.SourceIndex`（**禁止**使用 `start_point/end_point`，见 README 已知坑）。

### 3.2 图谱（`graph.json`）

```json
{ "version": 1, "generator": "cogen/0.1.0",
  "repo": {...}, "stats": {"nodes": N, "edges": M, "communities": K, "graph": {...}, "parse": {...}},
  "communities": [...], "godNodes": [...], "cycles": [...], "orphans": [...],
  "nodes": [{"id","kind","name","qualified","file","language","community","degree","inDegree","outDegree","start","end","doc"}],
  "edges": [{"source","target","relation","confidence","file","start"}] }
```

- **节点 id 位置无关**：符号 `<lang>:<relpath>#<qualified>.<kind>[#n]`；结构节点 `repo:` / `dir:` / `file:`；仓库外 `ext:`。
- **关系**：`contains` `defines` `imports` `calls` `extends` `implements` `instantiates` `references` `type_uses` `tests`。
- **置信度**：`EXTRACTED`（同文件/`self.x`）、`INFERRED`（经导入图解析）、`AMBIGUOUS`（多候选，前端默认隐藏）。

### 3.3 SQLite（一仓一库 `.cogen/db/<repoId>.sqlite`）

`meta` / `files` / `nodes` / `edges` / `communities` / `naming_cache` / `symbol_fts`(FTS5)。
任务表在全局 `.cogen/jobs.sqlite`。`nodes.extra` 里存 `degree/inDegree/outDegree/doc`，
按热度排序走 `json_extract(extra,'$.degree')`。

新增列走 `Store._migrate_files()` 的 `ALTER TABLE`（一仓一库，不值得引入迁移框架）。

## 4. HTTP 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/repos` | 提交目标（本地路径 / `owner/repo` / https / scp）→ 后台索引任务 |
| GET/DELETE | `/api/repos`、`/api/repos/{id}` | 列表 / 详情 / 删除（**只删索引与快照，绝不碰用户本地目录**） |
| GET | `/api/jobs/{id}`、`/api/jobs/{id}/events` | 任务状态 / SSE 进度（含终态回放与断流兜底） |
| GET | `/api/repos/{id}/files|file|cst` | 文件列表 / 源码 / 惰性 CST（无语法返回 415） |
| GET | `/api/repos/{id}/tree|analysis|graph|graph/neighbors|graph/path|graph/impact|search|symbol` | 图谱查询 |
| POST | `/api/repos/{id}/ask` | SSE 问答（tool / tool_result / delta / done / error） |
| GET/POST | `/api/repos/{id}/ai/status`、`/communities/name`、`/summary` | AI 状态 / 社区命名 / 架构摘要 |

## 5. 安全基线（实现位置）

| 措施 | 位置 |
|---|---|
| 目标白名单（拒 `ext::`、`file://`、`--xxx`、空白注入） | `security.check_git_url` + `resolve.resolve_target` |
| git 隔离（协议白名单、禁用系统/全局配置、token 走环境变量） | `ingest/clone.py` |
| 克隆体积/时长看门狗 | `ingest/clone.py` |
| 路径越界与符号链接穿越 | `security.ensure_within` + `api/routes_files.py` |
| **只有已索引文件可读**（`.env` 等读不到） | `api/routes_files.repo_context` + `store.get_file` |
| 解析进程隔离 + worker 环境变量裁剪 + 墙钟熔断 | `parse/pool.py` |
| 送 LLM 前密钥脱敏 | `security.redact_secrets` + `ai/llm._scrub_messages` |
| 工具集只读（Web 问答与 MCP 共用） | `graph/tools.py` + `ai/ask.TOOL_SPECS` + `mcp/server.py` |
| 仅监听 127.0.0.1 | `config.Settings.host` |

## 6. 前端结构

- 视图：`DirTreeView`（树 + SVG treemap + 语言分布）、`CstView`（源码 + 虚拟化 CST 树，双向联动）、
  `GraphView`（力导向 / 分层 DAG / 影响面三模式）、`AskView`（问答 + 工具轨迹 + 引用）。
- 渲染层抽象：`components/graph/renderers.tsx` 是图谱渲染的唯一入口，真实实现 `lazy()` 加载
  sigma / react-flow，测试注入替身，因此 jsdom 里从不加载 WebGL 依赖。
- 状态：`stores/ui.ts`（zustand）存选中文件/节点/任务；服务端数据一律走 react-query，
  queryKey 与 queryOptions 集中在 `api/*.ts`。
- 深链：`lib/deepLink.ts` 统一读写 URL 参数（`history.replaceState`，不引入路由库）。
- 渲染器性能：forceatlas2 迭代数按规模 200/120/60，Barnes-Hut 阈值降到 300（1500 节点 ×60 迭代 ≈ 360ms）。
