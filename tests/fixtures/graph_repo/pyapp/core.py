"""上层：依赖 helpers，并演示类方法自调用。"""

from .helpers import render

LENGTH = 3


class Engine:
    """引擎。"""

    def run(self) -> str:
        # self.step 是同类方法，应当解析成 EXTRACTED
        return self.step()

    def step(self) -> str:
        # render 来自 helpers.py，应当解析成 INFERRED
        return render("step")

    def noisy(self, values: list) -> int:
        """故意混入无法静态解析的调用：内建函数 + 局部变量上的方法调用。"""
        total = len(values)
        joined = "".join(values)
        return total + len(joined)
