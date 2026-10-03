"""包内数据资源的路径解析（wheel 安装与源码树同一逻辑）。

历史与理由
----------
数据目录（libraries/、contracts/）在 v0.4.0 起随包分发。以前放在仓库根，
用 `Path(__file__).parents[2]` 向上找 —— 源码树里碰巧成立，`pip install .`
之后 site-packages 里没有这些目录，所有加载缺陷库的子命令一律
FileNotFoundError（「pip install 后立刻可用」是本项目的生死线，wheel 冒烟
测试抓到的正是这个）。

importlib.resources 对 editable 与 wheel 安装给出同一个包内真实路径，
因此默认路径只认包内位置；调用方显式传入的
--library / --rules / --gold / --corpus 永远优先于默认值。
"""
from __future__ import annotations

from importlib import resources
from pathlib import Path


def data_dir(name: str) -> Path:
    """fieldmate 包内数据目录（"libraries" / "contracts"）。

    目录随包分发、必须存在；缺失说明安装损坏，按 H6 显式报错而不是静默返回。
    """
    d = Path(resources.files("fieldmate")) / name
    if not d.is_dir():
        raise FileNotFoundError(
            f"包内数据目录缺失：{d}（安装不完整？请重装：pip install --force-reinstall .）")
    return d


def library_dir() -> Path:
    return data_dir("libraries")


def contracts_dir() -> Path:
    return data_dir("contracts")


def prompt_path(name: str) -> Path:
    """包内任务模板（fieldmate/prompts/<name>），供 --llm-cmd 判断层使用。"""
    p = data_dir("prompts") / name
    if not p.is_file():
        raise FileNotFoundError(f"包内任务模板缺失：{p}（安装不完整？）")
    return p
