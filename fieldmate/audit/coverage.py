"""语料覆盖审计（能力①的另一半）——「我的语料够不够支撑我要做的事」

为什么 `gaps` 不够
-----------------
`gaps` 回答的是「**领域里**缺什么」，前提是语料确实覆盖了你要做的方向。
而覆盖这件事**可以被系统性地抓偏，且现有工具不会报警**：

    arXiv 上 abs:"Langevin equation" AND abs:"Allen-Cahn" -> 0 篇
    但 247 篇全文里逐篇打开核实，真正相关的只有 2 篇：
      hep-ph/0411207  "We derive the Langevin equation for the net baryon number
                       density, i.e. the Cahn-Hilliard equation."   <- 正是导师那条线
      1310.0293      Cahn-Hilliard model coupled to Langevin equations
    其余 9 篇是：参考文献里出现过这个短语(3)、统计物理的 Holstein/自旋模型(3)、
    机器学习的 Riemannian Langevin MCMC(3)。

**所以「命中 11 篇」是个误导性指标。** 真正决定统计功效的是那 2 篇。

命中位置就是那个区分信号
--------------------
本模块不只数命中篇数，还给每一条命中标注**出处**：

    TITLE  出现在标题里      -> 这篇论文的主题就是它，强证据
    BODY   出现在正文里      -> 是本文真正使用的东西
    REF    只出现在参考文献区 -> 几乎肯定只是「引用了别人」，弱证据
    NONE   没命中

REF-only 的命中**在统计上应当单列**，因为把它们计入「该术语有 N 篇支撑」
会让人以为有 N 篇可对比，而实际上能拿来对比的是 BODY+TITLE 那部分。

口径与局限（必须一起读）
--------------------
  * 词面统计。命中不等于相关：「Langevin」可能指随机过程而非 Langevin 方程；
    同义写法、不同符号约定都会让统计**偏低**。
  * 0 命中是强告警；非 0 命中**什么也证明不了**，只能说明「值得去看」。
  * 本模块不判定相关性 —— 那需要人读。判定归脚本，判断归 prompt。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["TermSpec", "TermCoverage", "CoverageReport", "audit_coverage",
           "report_markdown", "DEFAULT_TERMS"]

THIN = 5      # 少于这个数：横向对比的统计功效不足
ZERO = 0


@dataclass
class TermSpec:
    term: str
    note: str = ""


@dataclass
class TermCoverage:
    term: str
    note: str
    n_title: int = 0            # 命中且出现在标题里
    n_body: int = 0             # 命中且出现在正文里
    n_ref_only: int = 0         # 只在参考文献区命中
    n_untrimmable: int = 0      # 这批论文里没能裁掉参考文献区，口径不明
    samples: dict[str, list[str]] = field(default_factory=dict)

    @property
    def n_strong(self) -> int:
        """真正可用于横向对比的篇数。"""
        return self.n_title + self.n_body

    @property
    def n_any(self) -> int:
        return self.n_strong + self.n_ref_only

    @property
    def verdict(self) -> str:
        if self.n_strong == ZERO:
            return "NO_COVERAGE"
        if self.n_strong < THIN:
            return "THIN"
        return "OK"

    def to_dict(self) -> dict[str, Any]:
        return {
            "term": self.term, "note": self.note,
            "n_strong": self.n_strong, "n_title": self.n_title, "n_body": self.n_body,
            "n_ref_only": self.n_ref_only, "n_untrimmable": self.n_untrimmable,
            "n_any": self.n_any, "verdict": self.verdict,
            "samples": self.samples,
        }


@dataclass
class CoverageReport:
    n_papers: int
    n_with_fulltext: int
    terms: list[TermCoverage]

    def by_verdict(self, v: str) -> list[TermCoverage]:
        return [t for t in self.terms if t.verdict == v]

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_papers": self.n_papers,
            "n_with_fulltext": self.n_with_fulltext,
            "thin_threshold": THIN,
            "caveat": ("词面统计。命中不等于相关；同义写法与符号约定差异会让统计偏低。"
                       "0 命中是强告警，非 0 命中只说明「值得去看」，不证明相关。"
                       "n_ref_only（只在参考文献区出现）几乎肯定是「引用了别人」，"
                       "不应计入可对比篇数。"),
            "no_coverage": [t.term for t in self.by_verdict("NO_COVERAGE")],
            "thin": [t.term for t in self.by_verdict("THIN")],
            "ok": [t.term for t in self.by_verdict("OK")],
            "terms": [t.to_dict() for t in self.terms],
        }


# 课题组研究线的关键术语。显式写下来而不是自动抽取 ——
# 自动抽出来的实测是 Jalili/Wang/Kim/Ganji（作者名）、Stanford/Comput（引文残渣）、
# Then/Therefore（连接词），唯一那个「0 命中」的是作者名。**那个测量是垃圾。**
# 显式词表和 `libraries/defect_patterns.jsonl` 同一标准：每条都要能被独立复核。
DEFAULT_TERMS: list[TermSpec] = [
    TermSpec("Allen-Cahn", "核心模型，导师两篇论文的主方程"),
    TermSpec("Cahn-Hilliard", "四阶保体积模型，保体积类工作的对照组"),
    TermSpec("time-fractional", "导师 2025-2026 最活跃的线"),
    TermSpec("fractional", "同上，更宽的写法"),
    TermSpec("Langevin", "随机梯度流，导师核心工具之一"),
    TermSpec("Langevin equation", "同上，明确到方程形式"),
    TermSpec("damping limit", "Sine-Gordon 阻尼极限，另一条核心线"),
    TermSpec("Sine-Gordon", "同上"),
    TermSpec("Gray-Scott", "导师主页有归一化时间分数阶 Gray-Scott"),
    TermSpec("Lengyel-Epstein", "导师主页有归一化时间分数阶 Lengyel-Epstein"),
    TermSpec("Navier-Stokes", "导师主页有分数阶 Navier-Stokes"),
    TermSpec("fidelity term", "Allen-Cahn + 保真项，modified AC"),
    TermSpec("modified Allen-Cahn", "同上"),
    TermSpec("Voronoi", "导师的 Voronoi 晶格线"),
    TermSpec("mean curvature", "曲面重建的几何侧"),
    TermSpec("phase field", "本课题的方法族名"),
    TermSpec("surface reconstruction", "论文①的直接可比方向"),
    TermSpec("point cloud", "论文②与 pfdenoise 的定位"),
    TermSpec("mesh denoising", "论文②的经典基线"),
    TermSpec("level set", "同类基线方法"),
    TermSpec("topology optimization", "相场拓扑优化（人工骨植入体）"),
    TermSpec("gradient flow", "贯穿全部方法的上位概念"),
    TermSpec("volume preservation", "保体积，核心卖点之一"),
    TermSpec("energy stability", "导师「Cahn-Hilliard + 能量稳定性」那条线"),
]

THIN = 5      # 少于这个数：横向对比的统计功效不足
ZERO = 0


def _pattern(term: str) -> re.Pattern[str]:
    """允许 `phase field` / `phase-field` / `phase_field` 三种写法。"""
    parts = [x for x in re.split(r"[\s\-_]+", term.lower()) if x]
    return re.compile(r"\b" + r"[\s\-_]+".join(re.escape(x) for x in parts) + r"\b")


def audit_coverage(papers, specs: list[TermSpec] | None = None,
                   max_samples: int = 3) -> CoverageReport:
    """逐词审计语料覆盖。**只读，不联网，不判定相关性。**"""
    from ..sources.local import body_without_references

    specs = specs if specs is not None else DEFAULT_TERMS
    cov = {s.term: TermCoverage(term=s.term, note=s.note) for s in specs}
    pats = {s.term: _pattern(s.term) for s in specs}

    n_ft = 0
    for p in papers:
        ft = getattr(p, "fulltext", None)
        if not ft:
            continue
        n_ft += 1
        body, trimmed = body_without_references(ft)
        title = (getattr(p, "title", "") or "").lower()
        pid = getattr(p, "id", "?")
        for term, c in cov.items():
            pat = pats[term]
            in_title = bool(pat.search(title))
            in_body = bool(pat.search(body.lower()))
            in_ref = bool(pat.search(ft.lower())) and not (in_title or in_body)
            if in_title:
                c.n_title += 1
            elif in_body:
                c.n_body += 1
            elif in_ref:
                c.n_ref_only += 1
            if not trimmed:
                c.n_untrimmable += 1
            for kind, hit in (("title", in_title), ("body", in_body),
                              ("ref", in_ref)):
                if hit and len(c.samples.setdefault(kind, [])) < max_samples:
                    c.samples[kind].append(pid)

    return CoverageReport(n_papers=len(list(papers)), n_with_fulltext=n_ft,
                          terms=list(cov.values()))


def report_markdown(rep: CoverageReport) -> str:
    L = ["## 语料覆盖审计\n"]
    L.append(f"> {rep.n_with_fulltext} 篇有全文　"
             f"覆盖充分 {len(rep.by_verdict('OK'))} 条　"
             f"偏薄 {len(rep.by_verdict('THIN'))} 条　"
             f"无覆盖 {len(rep.by_verdict('NO_COVERAGE'))} 条\n")
    L.append(f"**可对比篇数 = 标题命中 + 正文命中。**只出现在参考文献区的命中"
             f"几乎肯定是「引用了别人」，不计入。\n")
    L.append("| 术语 | 可对比 | 标题 | 正文 | 仅参考文献 | 判定 | 说明 |")
    L.append("|---|---|---|---|---|---|---|")
    icon = {"OK": "OK", "THIN": "偏薄", "NO_COVERAGE": "**无覆盖**"}
    for t in sorted(rep.terms, key=lambda x: (x.verdict != "NO_COVERAGE",
                                              x.n_strong, x.term)):
        L.append(f"| {t.term} | **{t.n_strong}** | {t.n_title} | {t.n_body} | "
                 f"{t.n_ref_only} | {icon[t.verdict]} | {t.note} |")
    zero = rep.by_verdict("NO_COVERAGE")
    if zero:
        L.append(f"\n### 无覆盖的技术线（{len(zero)} 条）\n")
        for t in zero:
            L.append(f"- **{t.term}** —— {t.note}")
        L.append("\n这些不是「检索式写得不好」，而是语料里**确实没有**。"
                 "arXiv 检索式再改也补不上：这条线整体走期刊投稿。"
                 "需要 MathSciNet / zbMATH / 出版社数据库，且要有机构权限。")
    thin = rep.by_verdict("THIN")
    if thin:
        L.append(f"\n### 偏薄的技术线（<{THIN} 篇，{len(thin)} 条）\n")
        for t in thin:
            L.append(f"- **{t.term}**：可对比 {t.n_strong} 篇"
                     f"{'（另有 %d 篇仅参考文献命中）' % t.n_ref_only if t.n_ref_only else ''}")
        L.append("\n篇数这么少，横向对比的统计功效不足 —— "
                 "「这个领域集体没报 X」可能只是「能查到的就这几篇没报」。")
    L.append("\n### 口径\n")
    L.append("- 词面统计。命中**不等于**相关；同义写法、不同符号约定会让统计**偏低**。")
    L.append("- **0 命中是强告警；非 0 命中什么也证明不了**，只说明「值得去看」。")
    L.append("- 相关性判定需要人读，本模块不做。")
    untr = max((t.n_untrimmable for t in rep.terms), default=0)
    if untr:
        L.append(f"- 其中 {untr} 篇未能裁掉参考文献区，它们的口径与其它篇目不同，"
                 f"「仅参考文献」这一列对这些篇不可靠。")
    return "\n".join(L)
