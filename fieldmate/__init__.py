"""fieldmate —— 跨论文横向对比 + 缺陷库匹配（harness 无关）

核心保证：**不接 LLM 也能跑**。LLM 是可插拔的外部依赖，不是前置条件。
"""
__version__ = "0.4.0"

from .sources.arxiv import Paper, search, collect          # noqa: F401
from .match.patterns import load_library, load_rules, match_paper, match_all, Match  # noqa: F401
from .gaps.mine import mine_gaps, gaps_markdown, gaps_json, Gap  # noqa: F401
from .compare.matrix import (build_matrix, defect_stats, matrix_markdown,  # noqa: F401
                             matrix_json, matrix_csv, MatrixRow)

__all__ = ["Paper", "search", "collect", "load_library", "load_rules",
           "match_paper", "match_all", "Match", "build_matrix", "defect_stats",
           "matrix_markdown", "matrix_json", "matrix_csv", "MatrixRow", "mine_gaps", "gaps_markdown", "gaps_json", "Gap",
           "__version__"]