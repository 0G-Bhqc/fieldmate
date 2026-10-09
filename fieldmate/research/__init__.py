"""fieldmate.research — 突破点发现与可证伪实验设计器

核心职责：
1. 从旧论文（PDF 文本或五槽抽取结果）中逆向识别物理与数值瓶颈，发现下一步突破机会；
2. 将突破点或用户自然语言想法自动转化为符合契约的严谨可证伪预注册实验方案（Prereg）。
"""

from .breakthrough import (
    Breakthrough,
    breakthrough_markdown,
    discover_breakthroughs,
)
from .designer import (
    design_from_breakthrough,
    design_from_idea,
    design_from_paper,
)
from .diagnose import (
    DiagnosticDossier,
    diagnose_paper,
    dossier_markdown,
)

__all__ = [
    "Breakthrough",
    "breakthrough_markdown",
    "discover_breakthroughs",
    "design_from_idea",
    "design_from_breakthrough",
    "design_from_paper",
    "DiagnosticDossier",
    "diagnose_paper",
    "dossier_markdown",
]
