"""数据源：arXiv 检索、本地 PDF、arXiv 全文抓取、自动语料构建。"""
from .arxiv import Paper, search, collect, RateLimiter   # noqa: F401
from .corpus import (DomainFilter, HarvestResult, harvest, harvest_markdown,   # noqa: F401
                     CORE_CATEGORIES, ML_CATEGORIES, STRONG_TERMS, WEAK_TERMS)

__all__ = ["Paper", "search", "collect", "RateLimiter",
           "DomainFilter", "HarvestResult", "harvest", "harvest_markdown",
           "CORE_CATEGORIES", "ML_CATEGORIES", "STRONG_TERMS", "WEAK_TERMS"]
