"""LLM 适配层：可插拔的外部依赖，不是前置条件。

实现约束（从 Phase 1 起写死在这里，落地时未走样）：

* 核心**永不** import 具体 LLM SDK —— 本模块只有 ``subprocess``。
* 宿主通过 ``--llm-cmd "<命令>"`` 传入自己的调用命令；框架把任务写成 JSON
  喂给 stdin，从 stdout 读 JSON 回来，做协议与 schema 校验。
* ``--llm none`` 是默认值：不给 ``--llm-cmd``，全流程照常可跑。
* 判定永远在脚本里；LLM 只允许产出**候选**（docs/DESIGN.md §1）。
  本模块只负责「把候选安全地拿回来」——结构校验、幻觉闸门由各任务
  的调用方负责（如 ``extract.refine.validate_candidates``）。

PROTOCOL（v0.5.0 起生效）
------------------------
命令从 **stdin** 读入一个 JSON 对象::

    {"task": "<任务名>", "prompt": "<任务模板文本>", "data": {<任务数据>}}

向 **stdout** 写出**恰好一个** JSON 对象::

    {"ok": true, "result": {<任务定义的结构>}}     # 成功
    {"ok": false, "error": "<人类可读原因>"}        # 任务被拒/模型失败

约定：``prompt`` 是 fieldmate 提供的任务模板（含输出结构说明），
``data`` 是结构化输入；命令自行决定如何把两者交给模型（拼进消息、
走宿主 API 均可）。框架不假设模型、不假设网络，只假设这个 stdin/stdout 协议。
"""
from __future__ import annotations

import json
import subprocess


class LLMError(RuntimeError):
    """LLM 命令调用失败（非零退出 / 坏 JSON / 协议不符 / 任务被拒）。"""


def run_with_cmd(cmd: str, payload: dict, timeout: float = 180) -> dict:
    """通过外部命令调用宿主的 LLM，返回 ``result`` 字段。

    H6 失败显式：任何一种失败（非零退出、stdout 不是合法 JSON、协议不符、
    任务被拒）都抛 :class:`LLMError` 并带人类可读原因，绝不静默降级。
    """
    try:
        proc = subprocess.run(
            cmd, input=json.dumps(payload, ensure_ascii=False),
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=timeout, shell=True,
        )
    except subprocess.TimeoutExpired as e:
        raise LLMError(f"LLM 命令超时（>{timeout}s）：{cmd}") from e

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "")[-500:]
        raise LLMError(f"LLM 命令退出码 {proc.returncode}：{cmd}\n{tail}")
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        head = (proc.stdout or "")[:300]
        raise LLMError(f"LLM 命令 stdout 不是合法 JSON（{e}）：{cmd}\n{head}") from e
    if not isinstance(out, dict) or "ok" not in out:
        raise LLMError(f"LLM 命令输出不符合协议（缺 ok 字段）：{cmd}\n{(proc.stdout or '')[:300]}")
    if not out["ok"]:
        raise LLMError(f"LLM 任务被拒：{out.get('error', '(无原因)')}")
    if "result" not in out:
        raise LLMError(f"LLM 命令输出不符合协议（ok=true 但缺 result）：{cmd}")
    result = out["result"]
    if not isinstance(result, dict):
        raise LLMError(f"LLM result 必须是对象，实际 {type(result).__name__}：{cmd}")
    return result
