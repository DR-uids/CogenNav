# CogenNav

**English** | [简体中文](README.zh-CN.md)

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](#license)
[![MCP: stdio | streamable-http](https://img.shields.io/badge/MCP-stdio%20%7C%20streamable--http-6f42c1.svg)](https://modelcontextprotocol.io)

Turn any code repository — a GitHub URL or a local path — into a **drill-down CST** and a **queryable symbol knowledge graph**, then navigate it in your browser: directory tree → per-file syntax tree → cross-file call and dependency graph, with AI-assisted module naming, natural-language Q&A, and an MCP server for AI coding assistants.

CogenNav reads your code; it never runs it.

> **Status.** Milestones **M0–M5 are complete** (skeleton, ingestion, CST, graph, AI, MCP + export) and **M6** (hardening and delivery) is in progress. Milestones and acceptance criteria are tracked [below](#milestones); the data contracts and security boundary live in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

<details>
<summary><b>What's implemented so far (M1–M5)</b></summary>

- **M1 — ingestion.** `POST /api/repos` (local path, `owner/repo`, https, or scp-style; protocol allow-list and argument-injection guards), `GET /api/repos`, `GET|DELETE /api/repos/{id}`, `GET /api/jobs/{id}`, `GET /api/jobs/{id}/events` (SSE progress, including terminal-state replay); shallow clones with size and duration watchdogs and tokens read from the environment; `.gitignore`-aware traversal (file cap, binary/secret-file/symlink skipping).
- **M2 — CST.** `GET /api/repos/{id}/files`, `GET /api/repos/{id}/file`, `GET /api/repos/{id}/cst` (lazy subtrees, `format=sexp` fallback, `415` when no grammar applies); out-of-process parse pool (wall-clock watchdog, trimmed worker environment, automatic single-process fallback); parse statistics written back (node count, syntax error count).
- **M3 — graph.** `GET /api/repos/{id}/tree|analysis|graph|graph/neighbors|graph/path|graph/impact|search|symbol`; extractors for Python, TypeScript + TSX + JS, Go and Java (other languages fall back to generic heuristics); cross-file calls with three confidence levels (EXTRACTED / INFERRED / AMBIGUOUS); Louvain communities, import cycles, god nodes and orphan files; nodes, edges and parse stats persisted to SQLite with an FTS5 index.
- **M4 / M5 — AI, export, MCP.** `/ask` (SSE: tool trace + token stream + cited nodes), `/ai/status`, `/communities/name` (LLM naming with content-fingerprint caching and a deterministic fallback), `/summary`; `cogen export` (graph.json / GRAPH_REPORT.md / single-file graph.html); `cogen mcp` (stdio and streamable-http, 13 read-only tools).

</details>

## Highlights

- **Your code is never executed.** Parsing is `open(bytes)` plus tree-sitter; the only subprocess is `git`. Repository build scripts, tests and hooks are not run.
- **Isolated parsing.** A `spawn`ed worker pool with a per-chunk wall-clock circuit breaker (`py-tree-sitter` has no timeout API of its own) and a whitelisted worker environment that strips LLM keys and git tokens.
- **Honest confidence, not guesses.** Every cross-file call edge is EXTRACTED, INFERRED or AMBIGUOUS; calls that cannot be resolved statically are reported as `unresolved` instead of being invented.
- **Architecture at a glance.** Louvain communities, import cycles, god nodes and orphan files, over an addressable, shareable UI state (`/r/{repoId}?view=cst&file=...&node=0.1.2&line=12`).
- **Ask the graph.** Natural-language Q&A calls the same read-only query layer as the UI and streams a tool trace, tokens and cited nodes back over SSE.
- **Usable with AI assistants.** `cogen mcp` exposes that same read-only tool set to Claude Code, Cursor and other MCP clients — 13 tools and 3 resources, with **no execution or file-write entry point**.
- **Degrades by default.** No LLM key → deterministic community naming. No dedicated extractor → generic heuristics. Missing grammar → the file is still registered.
- **Bilingual UI, English by default.** Every label, empty state and error message ships in English and Chinese. The switch sits in the top bar, the choice survives a reload, and `?lang=zh` puts the language in the URL so a link can carry it. API errors follow the same choice through `Accept-Language`.

## Measured results

On [`psf/requests`](https://github.com/psf/requests) (122 files): indexing takes **2.5 s** and yields **1,306 nodes / 1,947 edges / 56 communities / 366 `calls` edges**, with a **67 % call-resolution rate** — close to the practical ceiling for static analysis without type inference. God nodes: `TestRequests`, `Response`, `Session`, `RequestsCookieJar`, `HTTPAdapter`. One genuine import cycle was found (11 files under `src/requests/*.py`). The fixture repository (`tests/fixtures/graph_repo`) pins a ≥ 80 % resolution baseline for CI.

On CST performance, a `depth=4` root slice of `tests/test_requests.py` (30,707 nodes) returns in **35 ms** and **31 KB gzipped** (the full tree is ~3 MB); lazily loading one subtree takes 0.5 ms.

## Requirements

- Python **3.10+**
- `git` on `PATH` (only for remote targets)
- Node.js + [pnpm](https://pnpm.io) for the web UI

## Quickstart

```bash
make setup     # create .venv and install backend + frontend dependencies
make dev       # backend on http://127.0.0.1:8765, frontend on http://127.0.0.1:5199
```

`make dev` starts FastAPI (8765, hot reload) and the Vite dev server (5199, proxying `/api` to the backend) together. For the production shape, run `make build-web` and then `cogen serve` — FastAPI serves `web/dist` directly.

## Command line

```bash
cogen status                # show configuration, data directory and indexed repositories
cogen index ./some/repo     # index without starting a server; prints a summary (--json to script it)
cogen index psf/requests --ref v2.31.0
cogen export --repo <repoId>  # all three artifacts; --format json|md|html for just one
cogen mcp --repo <repoId>     # start the MCP server over stdio (for AI coding assistants)
cogen mcp --http --port 8770  # or over Streamable HTTP (for a shared team endpoint)
cogen warm rust               # prefetch grammars (only needed with cogen[xlang])
```

Other development commands:

```bash
make test      # backend tests (the network marker is skipped by default)
make test-all  # backend + frontend tests
make lint      # ruff + mypy + tsc
make fmt       # auto-format
make demo      # one shot: index the fixture → export all three artifacts → print the entry points
PYTHONPATH=src .venv/bin/python scripts/bench.py psf/requests   # parse/CST benchmark
```

## Use with AI coding assistants (MCP)

`cogen mcp` exposes **13 read-only tools** (`repo_overview`, `search_symbols`, `get_symbol`, `neighbors`, `callers`, `callees`, `path_between`, `impact`, `file_tree`, `read_file`, `get_cst`, `list_communities`, `module_dependencies`) and 3 resources (`cogen://repo/overview`, `cogen://repo/analysis`, `cogen://repo/tree`). There is **no execution or file-write entry point** — an assistant can query the graph, never run your code.

Add a block like this to Claude Code, Cursor or any other MCP-capable client (point the paths at your own checkout):

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

Use the official Inspector while debugging: `npx @modelcontextprotocol/inspector cogen mcp --repo <repoId>`. Omit `--repo` to target the most recently indexed repository; if nothing has been indexed yet, the server tells you to run `cogen index <target>` first.

## Tech stack

| Layer | Choice |
|---|---|
| CST parsing | tree-sitter 0.26 + core-language wheels (about 19 languages, works offline); the 371-language pack is **off by default** (opt in with `pip install -e ".[xlang]"`) |
| Extraction & graph analysis | purpose-built per-language extractors + NetworkX (Louvain communities, cycle detection, PageRank) |
| Storage | SQLite (graph + FTS5 search + job state), one database per repository |
| Service | FastAPI + SSE for streaming indexing progress, bound to 127.0.0.1 only |
| Frontend | React 19 + Vite + TypeScript + Tailwind 4; Sigma (WebGL) for large graphs and React Flow/Dagre for layered DAGs |
| AI | OpenAI-compatible client (community naming, architecture summaries, graph tool-calling Q&A) |
| Assistant integration | MCP server (stdio / Streamable HTTP) exposing the **same** read-only tool set as Q&A |

## Design notes

- **Nobody's code runs.** Parsing is `open(bytes)` + tree-sitter; the only subprocess is `git`. No builds, tests or hooks from the indexed repository.
- **Parsing is isolated.** An out-of-process pool with a per-chunk wall-clock circuit breaker (`py-tree-sitter` ships no timeout API) and a whitelisted worker environment that strips LLM keys and git tokens.
- **One source of truth.** The web API, natural-language Q&A and MCP all reuse the read-only query functions in `cogen.graph.tools`.
- **Degrade rather than fail.** No LLM key → deterministic community naming; no dedicated extractor → generic heuristics; no grammar → the file is only registered.
- **Addressable state.** View state lives in the URL (`/r/{repoId}?view=cst&file=...&node=0.1.2&line=12`), so any view can be shared, bookmarked and navigated with the browser's back button.
- **One message catalog per side.** The UI reads `web/src/i18n/messages.en.ts` / `messages.zh.ts` (a missing key is a `tsc` error), the backend reads `cogen/i18n.py`; requests carry `Accept-Language`, and an indexing job captures the language at submit time so its background progress messages match the person who started it.

## Configuration

Every setting can be overridden through a `COGEN_`-prefixed environment variable — see [.env.example](.env.example) for size limits, clone timeouts, `GITHUB_TOKEN`, LLM endpoints and the redaction switch.

For the architecture overview, data contracts, API surface and security baseline, see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Project layout

```
src/cogen/
  cli.py config.py           # entry point and configuration
  ingest/                    # target resolution, shallow clone (hardened git), file traversal
  parse/                     # language registry, CST export, out-of-process parse pool
  extract/                   # per-language extractors + generic heuristics + call resolution
  graph/                     # schema / SQLite store / build / analyze / tools
  ai/                        # LLM client, community naming, Q&A
  mcp/                       # MCP server
  api/                       # FastAPI routes (repos/jobs/tree/cst/graph/ai)
  export/                    # graph.json / GRAPH_REPORT.md / graph.html
  i18n.py                    # message catalog + Accept-Language resolution
web/                         # React single-page app (UI copy in web/src/i18n)
tests/                       # unit tests + API tests + fixture repositories
```

## Milestones

| Milestone | Scope | Status | Key acceptance evidence |
|---|---|---|---|
| M0 | Skeleton and development loop | ✅ | `make setup/dev/lint/test` all green; every cache stays inside the workspace |
| M1 | Ingestion and hardened git | ✅ | Real shallow clone of `octocat/Hello-World`; `ext::`/`file://`/`--upload-pack` all rejected; SSE live and terminal-state replay |
| M2 | CST parsing and views | ✅ | 19 languages; `/cst` root slice of a 30,707-node file in 35 ms / 31 KB gzipped; pathological input trips the timeout breaker; worker environment trimmed |
| M3 | Extraction and knowledge graph | ✅ | Three confidence levels; fixture resolution ≥ 80 %; no dangling edges or duplicate ids; requests: 1,306 nodes / 56 communities / 67 % resolution |
| M4 | AI layer | ✅ | Community naming (LLM + content-fingerprint cache + deterministic fallback); `/ask` tool trace, token stream and citations; explicit degradation without a key |
| M5 | MCP / export / impact | ✅ | 13 read-only tools (verified callable from a stdio subprocess); `cogen export --format json\|md\|html`; incremental re-indexing re-parses only changed files (requests: 2.37 s → 0.12 s; 0.39 s for one changed file, with `parse.parsed == 1` asserted) |
| M6 | Hardening and delivery | 🔄 | Boundary tests (encoding / CRLF / very long lines / deep paths / Unicode / empty repo / submodules / broken symlinks); 70 % coverage gate (84 % measured); `make demo` runs end to end |

## Development

```bash
make test        # backend suite (236 tests; the network marker is skipped by default)
make test-all    # backend + frontend (135 tests)
make coverage    # backend coverage (70 % gate, ~84 % measured)
make lint        # ruff check + ruff format --check + mypy + tsc
make demo        # run the whole flow on the fixture repository
```

Tests that need real network access are marked and skipped by default:

```bash
pytest -q -m network          # real shallow clone of octocat/Hello-World
pytest -q -m "not network"    # offline-only suite (the CI default)
```

A note on coverage: parsing and extraction run inside `spawn`ed workers, whose coverage the parent process cannot observe, so `tests/test_extract_core.py` and `tests/test_extract_langs.py` call each language extractor **in-process** to cover that logic directly.

### Working inside a file sandbox

This project's development environment may only write **inside the workspace**, so all caches live in the repository; the `Makefile` sets them up for you:

| Variable | Value | Why |
|---|---|---|
| `PIP_CACHE_DIR` | `.cache/pip` | `~/Library/Caches` is outside the workspace |
| `TMPDIR` | `.cache/tmp` | same reason |
| `COGEN_HOME` | `.cogen` | repository snapshots / SQLite / exported artifacts |
| `TREE_SITTER_LANGUAGE_PACK_CACHE_DIR` | `.cogen/cache/grammars` | grammar downloads for the extended language pack |
| `PNPM_HOME` + `--store-dir .pnpm-store` | inside the workspace | the local npm cache is corrupted (EPERM), so pnpm is used throughout |

### Known pitfalls (please read before contributing)

- **Never touch `node.start_point` / `end_point` / `range`.** At the scale of a few thousand nodes, the native `Point` objects returned by py-tree-sitter 0.26.0 corrupt the heap, followed by a **bus error / segmentation fault** in the GC or at interpreter exit (reliably reproducible here). Always convert `start_byte`/`end_byte` with `cogen.parse.tscompat.SourceIndex`; a subprocess regression test guards this (`tests/test_parse.py::test_cst_serialization_does_not_crash_interpreter`).
- **The parse pool uses `spawn`.** If the parent is an interactive script without an `if __name__ == "__main__"` guard, workers fail to start; `ParsePool` then finishes its health check and **falls back to in-process parsing** (`meta.parse.mode == "serial"`) — still functional, but without timeout protection.
- **Node wrappers can't be compared with `is` / `==`.** Every attribute access builds a fresh Python wrapper (`is` is always `False` in practice); use `tscompat.same_node(a, b)`, which compares the native `id`, to test node identity.
- **tree-sitter field names often differ from node type names.** Java `implements` yields a `super_interfaces` node whose field is `interfaces`; `field_declaration`'s `modifiers` is not reachable as a field and must be found by scanning children; Go grouped imports add an `import_spec_list` layer, so imports must be collected with `ctx.walk()` rather than `named_children`. When writing an extractor for a new language, print `field_name_for_child` before you start.
- **Multi-name declarations only expose the last name.** For Go `const A, B = 1, 2` the `name` field yields only `B`; walk the siblings for the rest.
- **Truncated source can collapse into one `ERROR` node.** With Java `class A { void f(`, not even a `class_declaration` exists — extractors must treat "nothing extracted" as a normal outcome.

## Known limitations

- **Call resolution is not 100 %.** Without type inference, `obj.method()` where `obj` is a local variable or parameter is never guessed and counts as `unresolved`. Measured: 67 % on requests, ≥ 80 % on the fixture. Pushing higher requires local type inference.
- **Three confidence levels, no cross-language calls.** Edges are not drawn across languages (for example Python ↔ JS), which is very rare within a single repository anyway.
- **Syntax trees are not persisted.** CST views re-parse on demand (with an LRU cache of 8 files), so very large repositories pay that cost repeatedly.
- **The extended language pack (371 languages) is off by default.** It needs `pip install -e ".[xlang]"`, and the first parse downloads **native** parsers from the network.
- **Incremental indexing only saves parsing and extraction.** Unchanged files are skipped via per-file sha256 plus an extraction cache (requests: 2.37 s → **0.12 s** on a second run, 0.39 s after changing one file), but graph building and community analysis still rerun in full — pure in-memory work measured in tens of milliseconds.
- **No browser-side screenshot regression tests.** The frontend is gated by vitest plus a production build; visual verification is manual.

## License

MIT. See [pyproject.toml](pyproject.toml) for the package metadata.
