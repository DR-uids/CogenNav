"""最底层工具，没有任何依赖。"""

PREFIX = "fmt"


def fmt(value: str) -> str:
    """把值格式化成统一风格。"""
    return f"{PREFIX}:{value}"
