"""中间层：依赖 utils。"""

from .utils import fmt


def render(text: str) -> str:
    """渲染文本（跨文件调用 utils.fmt）。"""
    return fmt(text)
