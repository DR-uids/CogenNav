"""全局配置：全部项均可由 `COGEN_` 前缀的环境变量或 .env 覆盖。

沙箱前提：`home`（默认 `<CWD>/.cogen`）必须落在工作区内，因为工作区外的写入会被拒。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="COGEN_", env_file=".env", extra="ignore")

    # ── 服务 ────────────────────────────────────────────────────────
    home: Path = Path(".cogen")
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "info"

    # ── 索引规模与防护上限（见计划 §8）──────────────────────────────
    max_files: int = 20_000
    max_file_bytes: int = 1_048_576
    max_total_bytes: int = 2_147_483_648
    clone_timeout_s: float = 300.0
    parse_chunk_timeout_s: float = 120.0
    #: 整块超时后逐个文件重试时的单文件超时
    parse_individual_timeout_s: float = 10.0
    parse_workers: int = 0  # 0 = cpu_count - 1

    # ── git（可选 token，仅通过环境变量传入，绝不写入图谱或日志）────
    git_token: str | None = None

    # ── LLM（OpenAI 兼容；缺 api_key 时 AI 功能降级）─────────────────
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str = "deepseek-chat"
    llm_max_tokens: int = 2048
    llm_timeout_s: float = 60.0
    llm_redact: bool = True

    @field_validator("home")
    @classmethod
    def _expand_home(cls, v: Path) -> Path:
        return v.expanduser()

    # ── 派生路径 ────────────────────────────────────────────────────
    @property
    def root(self) -> Path:
        """COGEN_HOME 的绝对路径（相对路径按当前工作目录解析）。"""
        return self.home if self.home.is_absolute() else (Path.cwd() / self.home)

    @property
    def repos_dir(self) -> Path:
        return self.root / "repos"

    @property
    def db_dir(self) -> Path:
        return self.root / "db"

    @property
    def exports_dir(self) -> Path:
        return self.root / "exports"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    def ensure_dirs(self) -> None:
        for d in (self.root, self.repos_dir, self.db_dir, self.exports_dir, self.cache_dir):
            d.mkdir(parents=True, exist_ok=True)

    # ── 便捷判定 ────────────────────────────────────────────────────
    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)

    def effective_parse_workers(self) -> int:
        if self.parse_workers > 0:
            return self.parse_workers
        return max(1, (os.cpu_count() or 2) - 1)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """测试用：清掉配置缓存，让新环境变量生效。"""
    get_settings.cache_clear()
