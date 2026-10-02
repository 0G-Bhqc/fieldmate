"""主题空间审计：把「这个方向在语料里有多少」变成**可分诊的证据清单**

为什么不做成「数一数有几个词」
-----------------------------
这个坑我连踩三次，每一次形态不同，但根子是一个：

  1. 标题级搜 differentiable/inverse design -> 0 篇 -> 宣称「这条线是空的」
     实际全文级 differentiable 有 52 篇。**只搜标题**。
  2. 全文级搜 -> 「adjoint 42 篇」-> 差点当成 42 篇可微求解器工作
     实际多数是**泛函分析里的伴随算子**（"Duality estimates for subdiffusion"、
     "Rigidity and existence of Busemann profiles"）。**术语碰撞**。
  3. 搜具名框架 prisms -> 命中 2 篇 -> 实际匹配到的是
     "hexagonal prisms"（Wulff 形状）和"tetrahedra, prisms, or hexahedra"
     （网格单元）。**子串误匹配**。

而同期真查出来的：
  * FEniCS 实际被 6 篇用了，其中一篇明写
    "we implemented a classical phase-field topology optimization solver in FEniCS"
    —— 就在导师的拓扑优化线上；
  * 「可微相场求解器」这个方向**薄、且没人给它命名立题**，但**不是空的**。

结论：**词频统计不能支撑任何结论。** 能支撑的只有一件事 ——
把候选篇目连同「匹配到的原文片段」一起摆出来，让人几十秒内分诊完，
不必逐篇打开 PDF。

所以本模块的产出是证据清单 + 原文片段，**不是**一个数字结论。
报告里会明写「词层面，词义未核验」。

两个防碰撞机制
--------------
1. **共现要求**（`require_all`）：一个探针可以声明「必须同时命中 A 组和 B 组」。
   `adjoint` 单独出现不算数，必须同时出现 differentiable solver / gradient-based
   / inverse problem 之一才计入 —— 这样「伴随算子」和「伴随法求梯度」就分开了。
2. **出处分离**：只出现在参考文献区的命中单独计数，不计入「正文命中」。
   引言里提一句别人的工作，不等于这篇在讲它。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["TopicProbe", "TopicHit", "TopicAudit", "audit_topics", "report_markdown",
           "DEFAULT_PROBES"]

CTX = 150          # 片段长度：够看出上下文，又不至于把报告撑爆
REF_ONLY_LABEL = "仅参考文献"


def _rx(pat: str) -> re.Pattern[str]:
    return re.compile(pat, re.I)


@dataclass
class TopicProbe:
    """一个主题探针。

    name    主题名（写给人看的，必须能被独立复核）
    groups  词组列表；**每组都要至少命中一处**才算这篇论文属于该主题。
            这样可以用「共现」消解术语碰撞。
    note    为什么关心这个方向
    """
    name: str
    groups: list[list[str]]
    note: str = ""

    def compiled(self) -> list[re.Pattern[str]]:
        return [_rx("|".join(g)) for g in self.groups]

    def all_groups_hit(self, low: str) -> tuple[bool, dict[int, str]]:
        found: dict[int, str] = {}
        for i, g in enumerate(self.groups):
            m = _rx("|".join(g)).search(low)
            if not m:
                return False, {}
            found[i] = m.group(0)
        return True, found


@dataclass
class TopicHit:
    paper_id: str
    year: int | None
    title: str
    in_title: bool
    matched: dict[int, str]                 # 组序号 -> 实际命中的词
    context: str = ""                       # 原文片段
    in_references_only: bool = False


@dataclass
class TopicAudit:
    n_papers: int
    n_with_fulltext: int
    n_untrimmable: int
    topics: list[tuple[TopicProbe, list[TopicHit]]] = field(default_factory=list)

    def body_count(self, name: str) -> int:
        for p, hits in self.topics:
            if p.name == name:
                return sum(1 for h in hits if not h.in_references_only)
        return 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_papers": self.n_papers,
            "n_with_fulltext": self.n_with_fulltext,
            "n_untrimmable": self.n_untrimmable,
            "disclaimer": ("词层面匹配，**词义未核验**。已用「共现要求」压低术语碰撞，"
                           "但最终判断仍需人读每条命中的原文片段。"
                           "参考文献区已预先裁掉，故本表不含仅参考文献命中；"
                           "未能裁掉的篇目见 n_untrimmable，其命中可能偏高。"),
            "topics": [{
                "name": p.name, "note": p.note,
                "n_body": sum(1 for h in hs if not h.in_references_only),
                "n_title": sum(1 for h in hs if h.in_title),
                "n_ref_only": sum(1 for h in hs if h.in_references_only),
                "hits": [{"paper_id": h.paper_id, "year": h.year,
                          "title": h.title, "in_title": h.in_title,
                          "matched": [m for m in h.matched.values()],
                          "context": h.context} for h in hs],
            } for p, hs in self.topics],
        }


# 课题组关心的主题探针。显式写下来 + 共现要求，与缺陷库同一标准。
DEFAULT_PROBES: list[TopicProbe] = [
    TopicProbe(
        "可微求解器（伴随法/梯度）",
        [["adjoint", r"adjoint[- ]based", r"adjoint method"],
         ["differentiable solver", "differentiable simulation", "gradient-based",
          r"different\w*\s+(?:discretiz|solver|scheme)", "variational integrator"]],
        "导师拓扑优化线的工具链：把相场求解纳入求导才能做参数反演"),
    TopicProbe(
        "相场反问题/参数反演",
        [["inverse problem", "inverse design", "inverse modelling", "inverse modeling",
          "parameter identification", "parameter calibration", "data assimilation"],
         ["phase", "cahn", "allen", "free energy", "order parameter", "topolog"]],
        "对相场参数做反演，是「可微求解器」的主要用武之地"),
    TopicProbe(
        "神经网络替代相场求解",
        [["neural operator", "physics-informed", "pinn", "deep learning",
          "machine learning", "neural network", "surrogate", "fourier neural"],
         ["phase", "cahn", "allen", "free energy", "order parameter"]],
        "ML 侧替代方案，是本方向 2025–2026 最拥挤的一条"),
    TopicProbe(
        "相场拓扑优化",
        [["topolog(?:y|ical) optimi", "structural optimization", "shape optimization",
          "level set optimi"],
         ["phase", "cahn", "allen", "topology optimization"]],
        "导师的人工骨植入体方向"),
    TopicProbe(
        "FEniCS/有限元实现",
        [[r"\bFEniCSx?\b", r"\bPETSc\b", r"\bdeal\.II\b", r"\bJAX-?FEM\b"],
         ["implement", "implemented", "implementation", "solver", "discretiz", "package"]],
        "现成 FEM 框架的采用情况 —— 有代码就更容易被复现"),
]


def audit_topics(papers, probes: list[TopicProbe] | None = None) -> TopicAudit:
    """逐篇匹配探针，**给出原文片段**。只读，不联网，不判定相关性。"""
    from ..sources.local import body_without_references

    probes = probes if probes is not None else DEFAULT_PROBES
    papers = list(papers)
    out: list[tuple[TopicProbe, list[TopicHit]]] = []
    # 先算一次全文篇数与「裁不掉参考文献区」的篇数。
    # 早先版本把 n_untrim 累加放在探针循环**内**，结果被乘了探针数
    # （5 个探针 -> 22 篇报成 110 篇）。计数类的东西不能按探针重复累加。
    n_ft = sum(1 for p in papers if getattr(p, "fulltext", None))
    n_untrim = 0
    bodies: list[tuple[Any, str, str, bool]] = []
    for p in papers:
        ft = getattr(p, "fulltext", None)
        if not ft:
            continue
        body, trimmed = body_without_references(ft)
        if not trimmed:
            n_untrim += 1
        bodies.append((p, (getattr(p, "title", "") or ""), body, trimmed))

    for probe in probes:
        hits: list[TopicHit] = []
        for p, title, body, _trimmed in bodies:
            ok_t, m_t = probe.all_groups_hit(title.lower())
            ok_b, m_b = probe.all_groups_hit(body.lower())
            if not (ok_t or ok_b):
                continue
            # 片段取**第一处实际命中**的上下文，取自正文或标题
            src = body if ok_b else title
            m = re.search("|".join(probe.groups[1] if ok_b else probe.groups[0]),
                          src, re.I)
            ctx = ""
            if m:
                ctx = re.sub(r"\s+", " ", src[max(0, m.start() - CTX // 2):
                                                m.end() + CTX // 2]).strip()
            hits.append(TopicHit(
                paper_id=getattr(p, "id", "?"), year=getattr(p, "year", None),
                title=title[:90], in_title=ok_t,
                matched=m_t if ok_t else m_b, context=ctx,
                in_references_only=False))
        out.append((probe, hits))

    return TopicAudit(n_papers=len(papers), n_with_fulltext=n_ft,
                      n_untrimmable=n_untrim, topics=out)


def report_markdown(au: TopicAudit, show: int = 4) -> str:
    L = ["## 主题空间审计\n"]
    L.append(f"> {au.n_with_fulltext} 篇有全文　探针 {len(au.topics)} 个\n")
    L.append("> ⚠ **词层面匹配，词义未核验。**已用「共现要求」压低术语碰撞，"
             "每条命中都附了原文片段 —— **分诊看片段，不要只看计数**。\n")
    # 参考文献区已由 body_without_references 预先裁掉，所以「仅参考文献命中」
    # 天然不可能出现在这里；再单列一列只会永远是 0，让人以为坏了。
    L.append("| 主题 | 正文命中 | 标题命中 |")
    L.append("|---|---|---|")
    for probe, hits in au.topics:
        nb = sum(1 for h in hits if not h.in_references_only)
        nt = sum(1 for h in hits if h.in_title)
        L.append(f"| {probe.name} | **{nb}** | {nt} |")
    if au.n_untrimmable:
        L.append(f"\n> 口径：{au.n_untrimmable} 篇未能裁掉参考文献区，"
                 f"它们的「正文」里含引用条，可能带来**偏高的**命中。")
    for probe, hits in au.topics:
        L.append(f"\n### {probe.name}\n")
        L.append(f"{probe.note}\n")
        show_list = [h for h in hits if not h.in_references_only][:show]
        if not show_list:
            L.append("**正文命中 0。**（若确有相关工作，说明它不在这份语料里；"
                     "若认为应该有，那是**语料缺口**，值得去补检索式。）\n")
            continue
        for h in show_list:
            y = f"{h.year}　" if h.year else ""
            L.append(f"- `{h.paper_id}`　{y}{h.title}")
            L.append(f"  <sub>命中：{', '.join(h.matched.values())}</sub>")
            if h.context:
                L.append(f"\n  > …{h.context}…\n")
            else:
                L.append("")
    return "\n".join(L)
