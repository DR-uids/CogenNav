SHELL := /bin/bash
PROJ := $(CURDIR)

# ── 沙箱前提：所有缓存/临时目录必须落在工作区内（工作区外写入被拒）──────
export PIP_CACHE_DIR := $(PROJ)/.cache/pip
export TMPDIR := $(PROJ)/.cache/tmp
export COGEN_HOME := $(PROJ)/.cogen
export TREE_SITTER_LANGUAGE_PACK_CACHE_DIR := $(PROJ)/.cogen/cache/grammars
export PNPM_HOME := $(PROJ)/.pnpm-home
export PYTHONPATH := $(PROJ)/src
export PATH := $(PNPM_HOME):$(PROJ)/.venv/bin:$(PATH)

PY := $(PROJ)/.venv/bin/python
PIP := $(PROJ)/.venv/bin/pip
PNPM := pnpm

.DEFAULT_GOAL := help

.PHONY: help setup venv py-deps web-deps dev api web build-web test test-all coverage lint fmt index demo mcp clean

help: ## 显示可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: venv py-deps web-deps ## 一次性安装后端 + 前端依赖

venv: ## 创建虚拟环境
	@test -d .venv || python3 -m venv .venv
	@mkdir -p .cache/pip .cache/tmp .cogen .pnpm-home

py-deps: venv ## 安装 Python 依赖（可编辑安装）
	$(PIP) install --upgrade pip
	$(PIP) install -e ".[dev]"

web-deps: ## 安装前端依赖（npm 在本机缓存损坏，统一用 pnpm）
	cd web && $(PNPM) install --store-dir $(PROJ)/.pnpm-store

api: ## 只起后端（127.0.0.1:8765，热重载）
	$(PY) -m uvicorn cogen.api.app:app --host 127.0.0.1 --port 8765 --reload

web: ## 只起前端 dev server（127.0.0.1:5199，/api 代理到后端）
	cd web && $(PNPM) dev

dev: ## 同时起后端 + 前端（Ctrl-C 一并退出）
	bash scripts/dev.sh

build-web: ## 构建前端产物到 web/dist（由 FastAPI 静态托管）
	cd web && $(PNPM) build

test: ## 后端测试（默认跳过 network 标记）
	$(PY) -m pytest -q -m "not network"

test-all: test ## 后端 + 前端测试
	cd web && $(PNPM) test --run

coverage: ## 后端覆盖率（门槛 70%；worker 进程内的代码由直连单测覆盖）
	$(PY) -m pytest -q -m "not network" --cov=cogen --cov-report=term-missing:skip-covered --cov-fail-under=70

lint: ## 后端 ruff + mypy，前端 tsc
	$(PY) -m ruff check src tests
	$(PY) -m ruff format --check src tests
	$(PY) -m mypy src/cogen
	cd web && $(PNPM) typecheck

fmt: ## 自动格式化
	$(PY) -m ruff check --fix src tests
	$(PY) -m ruff format src tests

index: ## 索引一个仓库：make index TARGET=owner/repo 或 TARGET=/abs/path
	$(PY) -m cogen.cli index "$(TARGET)"

demo: ## 用 tests/fixtures 里的仓库跑通全流程
	bash scripts/demo.sh

mcp: ## 以 stdio 启动 MCP server
	$(PY) -m cogen.cli mcp --stdio

clean: ## 清理构建缓存（保留 .cogen 数据）
	rm -rf .cache .pytest_cache .mypy_cache .ruff_cache web/dist
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
