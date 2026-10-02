"""让测试与**运行目录无关**。

问题
----
早先跑 `pytest tests/` 一律先 `cd` 到项目根，所以 143 个测试全过。
换个目录用绝对路径跑就 `ModuleNotFoundError: No module named 'rharness'` ——
因为 `rharness` 并没有安装（`pip install -e .` 没人跑过），
而 Python 只把**当前工作目录**加进 `sys.path`。

为什么这不是小事
----------------
本项目对外的核心承诺是「挂到任意 Agent Harness 都能用」。
harness 启动子进程时的工作目录**不会**是仓库根 —— 它可能是用户 home、
可能是某个 skill 目录、可能是临时目录。于是一个「143 测试全绿」的项目，
换个地方就整个 import 不进来。

更糟的是这类故障**只报 ModuleNotFoundError，不提示任何原因**，
读的人会以为包坏了，而不是「测试依赖了 cwd」。

做法
----
在项目根放 conftest.py，把仓库根显式插进 `sys.path`。
这是 pytest 的标准做法，不改变被测代码的行为，也不需要先安装。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
