"""LLM 适配层：可选依赖，不是前置条件。

Phase 1 未启用。设计约束（写在这里以免 Phase 2 走偏）：

* 核心**永不** import 具体 LLM SDK。
* Harness 通过 ``--llm-cmd`` 传入自己的调用命令（读 stdin 的 prompt，往 stdout 写 JSON），
  框架只负责拼 prompt、解析 JSON、做 schema 校验。
* ``--llm none`` 是默认值，必须保证全流程可用。
* 判定永远在脚本里；LLM 只允许产出候选（见 docs/DESIGN.md §1）。
"""

PROTOCOL = """协议：命令从 stdin 读入 {"task":..., "payload":...}，
向 stdout 写出 {"ok":true,"result":...} 或 {"ok":false,"error":"..."}。"""


def run_with_cmd(cmd, payload: dict) -> dict:
    """通过外部命令调用任意 harness 的 LLM。Phase 2 实现。"""
    raise NotImplementedError("Phase 2 实现；Phase 1 请用 --llm none")
