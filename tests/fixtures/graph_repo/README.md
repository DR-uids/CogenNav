# graph_repo fixture

用于验证 M3 抽取/建图/分析的最小多语言仓库：

- `pyapp/`：跨文件调用（core → helpers → utils）、类方法自调用（`self.step()`）、
  循环导入（cycle_a ↔ cycle_b）、测试文件（`tests/test_core.py`）、
  以及两类**故意无法解析**的调用（内建函数 + 局部变量上的方法调用）。
- `web/`：TypeScript 跨文件调用（index.ts → helper.ts）。
