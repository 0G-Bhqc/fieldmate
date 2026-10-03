"""数据源：arXiv 检索、本地 PDF、arXiv 全文抓取、自动语料构建。"""
from .arxiv import Paper, RateLimiter, collect, search  # noqa: F401
from .corpus import (  # noqa: F401
                     CORE_CATEGORIES,
                     ML_CATEGORIES,
                     STRONG_TERMS,
                     WEAK_TERMS,
                     DomainFilter,
                     HarvestResult,
                     harvest,
                     harvest_markdown,
)

__all__ = ["Paper", "search", "collect", "RateLimiter",
           "DomainFilter", "HarvestResult", "harvest", "harvest_markdown",
           "CORE_CATEGORIES", "ML_CATEGORIES", "STRONG_TERMS", "WEAK_TERMS"]
