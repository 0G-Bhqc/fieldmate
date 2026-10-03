"""缺陷库加载与规则匹配（纯确定性）。"""
from .patterns import Match, load_library, load_rules, match_all, match_paper  # noqa: F401

__all__ = ["load_library", "load_rules", "match_paper", "match_all", "Match"]
