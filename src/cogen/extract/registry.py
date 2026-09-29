"""抽取器注册表：语言 id → Extractor，未注册的语言回落到通用启发式抽取器。"""

from __future__ import annotations

from .base import Extractor

_REGISTRY: dict[str, Extractor] = {}
_loaded = False


def register(extractor: Extractor) -> None:
    _REGISTRY[extractor.language] = extractor
    for alias in getattr(extractor, "aliases", ()):  # 例如 typescript 同时服务 tsx
        _REGISTRY[alias] = extractor


def _load_defaults() -> None:
    global _loaded
    if _loaded:
        return
    _loaded = True
    from . import generic, go, java, python, typescript

    for module in (python, typescript, go, java):
        register(module.EXTRACTOR)
    register(generic.EXTRACTOR)


def get_extractor(language: str) -> Extractor:
    """取语言的抽取器；没有专用实现时返回通用启发式抽取器。"""
    _load_defaults()
    return _REGISTRY.get(language) or _REGISTRY["generic"]


def has_dedicated_extractor(language: str) -> bool:
    _load_defaults()
    found = _REGISTRY.get(language)
    return found is not None and found.language == language


def registered_languages() -> list[str]:
    _load_defaults()
    return sorted(_REGISTRY)
