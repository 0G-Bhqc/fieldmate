"""缺陷库加载与规则匹配（纯确定性）。"""
from .patterns import (load_library, load_rules, match_paper,  # noqa: F401
                       match_all, Match)

__all__ = ["load_library", "load_rules", "match_paper", "match_all", "Match"]