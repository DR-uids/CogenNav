"""测试文件：应当产生 tests 边。"""

from pyapp.core import Engine
from pyapp.helpers import render


def test_run() -> None:
    engine = Engine()
    assert engine.run()
    assert render("x")
