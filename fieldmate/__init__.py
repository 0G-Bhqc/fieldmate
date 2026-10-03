"""fieldmate —— 跨论文横向对比 + 缺陷库匹配（harness 无关）

核心保证：**不接 LLM 也能跑**。LLM 是可插拔的外部依赖，不是前置条件。
"""
__version__ = "0.5.0"

from .compare.matrix import (  # noqa: F401
                             MatrixRow,
                             build_matrix,
                             defect_stats,
                             matrix_csv,
                             matrix_json,
                             matrix_markdown,
)
from .gaps.mine import Gap, gaps_json, gaps_markdown, mine_gaps  # noqa: F401
from .match.patterns import Match, load_library, load_rules, match_all, match_paper  # noqa: F401
from .sources.arxiv import Paper, collect, search  # noqa: F401

__all__ = ["Paper", "search", "collect", "load_library", "load_rules",
           "match_paper", "match_all", "Match", "build_matrix", "defect_stats",
           "matrix_markdown", "matrix_json", "matrix_csv", "MatrixRow",
           "mine_gaps", "gaps_markdown", "gaps_json", "Gap",
           "__version__"]
