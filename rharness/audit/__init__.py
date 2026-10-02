"""语料覆盖审计包。

两个子模块，分工不同：
  * `coverage` —— 给定**术语**查覆盖（这个术语在语料里有没有支撑）
  * `topics`   —— 给定**主题**查空间（哪些方向有、哪些方向一篇都没有）

另与 `gaps` 的分工：gaps 看**领域里**缺什么（报告规范缺失），
这两个看**我的语料**够不够。语料可以被系统性地抓偏，而 gaps 不会为此报警。
"""
from .coverage import (CoverageReport, DEFAULT_TERMS, TermCoverage, TermSpec,
                       audit_coverage, report_markdown)
from .topics import (DEFAULT_PROBES, TopicAudit, TopicHit, TopicProbe,
                     audit_topics, report_markdown as report_topics_markdown)

__all__ = ["CoverageReport", "DEFAULT_TERMS", "TermCoverage", "TermSpec",
           "audit_coverage", "report_markdown",
           "DEFAULT_PROBES", "TopicAudit", "TopicHit", "TopicProbe",
           "audit_topics", "report_topics_markdown"]
