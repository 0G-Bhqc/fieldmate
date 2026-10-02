"""精进点生成：把「哪里有缺失」转成「所以你能做什么」。"""
from .mine import mine_gaps, gaps_markdown, gaps_json, Gap, GENRE_SENSITIVITY  # noqa: F401

__all__ = ["mine_gaps", "gaps_markdown", "gaps_json", "Gap", "GENRE_SENSITIVITY"]