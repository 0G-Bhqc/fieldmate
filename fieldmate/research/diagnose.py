"""diagnose.py — 论文综合学术与数值体检引擎 (借鉴 PaperQA / SWE-agent 诊断闭环架构)

一键流水线：
1. 槽位解析：按阅读目的（复现/超越/引用）抽取核心假设与协议槽位；
2. 缺陷对拍：对拍 19 项相场/几何缺陷模式库，标出潜在数值炸弹；
3. 突破挖掘：匹配 5 大理论突破原型，逆向推导下一步改进空间；
4. 方案合成：一键自动生成 100% 合规的可证伪预注册实验方案。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ..extract.disclose import reading_card_l1, reading_card_l2
from ..extract.slots import extract_slots
from ..match.patterns import load_library, load_rules, match_paper
from ..sources.arxiv import Paper
from ..sources.local import parse_pdf
from .breakthrough import Breakthrough, breakthrough_markdown, discover_breakthroughs
from .designer import design_from_breakthrough

__all__ = ["DiagnosticDossier", "diagnose_paper", "dossier_markdown"]


@dataclass
class DiagnosticDossier:
    paper_id: str
    paper_path: str
    reading_card: dict[str, Any] = field(default_factory=dict)
    matched_defects: list[dict[str, Any]] = field(default_factory=list)
    breakthroughs: list[Breakthrough] = field(default_factory=list)
    recommended_prereg: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["breakthroughs"] = [b.to_dict() for b in self.breakthroughs]
        return d


def diagnose_paper(
    path: str | Path,
    purpose: str = "beat",
    auto_design: bool = True,
    library_path: str | None = None,
    rules_path: str | None = None,
) -> DiagnosticDossier:
    """对单篇论文执行一键端到端综合学术与数值体检。"""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"论文文件不存在：{p}")

    text, _backend = parse_pdf(p)
    if not text:
        raise ValueError(f"无法从该 PDF 提取文本内容：{p}")

    paper_id = p.stem
    paper = Paper(id=paper_id, title=paper_id, abstract=text[:1000])

    # 1. 阅读卡槽位提取
    slots = extract_slots(paper, text, per_slot=4)
    card = reading_card_l1(slots)
    card.update(reading_card_l2(slots))

    # 2. 缺陷对拍
    lib = load_library(library_path)
    rules = load_rules(rules_path)
    m = match_paper(paper, text, lib, rules)
    matched_defects = []
    rule_map = {r["id"]: r for r in rules.get("rules", [])}
    for d_id in m.hit_ids:
        for item in lib:
            if item["id"] == d_id:
                r_info = rule_map.get(d_id, {})
                matched_defects.append({
                    "id": d_id,
                    "title": item["title"],
                    "category": item.get("class", "unknown"),
                    "severity": item.get("severity", "MEDIUM"),
                    "pattern": r_info.get("pattern", ""),
                    "description": item.get("detection", ""),
                })
                break

    # 3. 突破点挖掘
    bts = discover_breakthroughs(text, paper_id=paper_id)

    # 4. 推荐预注册方案
    recommended_prereg_dict = {}
    if auto_design and bts:
        # 选取首个最高置信度突破点自动设计实验
        top_bt = bts[0]
        prereg = design_from_breakthrough(top_bt, exp_id=f"exp-diag-{paper_id[:16]}")
        recommended_prereg_dict = prereg.to_dict()

    return DiagnosticDossier(
        paper_id=paper_id,
        paper_path=str(p),
        reading_card=card,
        matched_defects=matched_defects,
        breakthroughs=bts,
        recommended_prereg=recommended_prereg_dict,
    )


def dossier_markdown(dossier: DiagnosticDossier) -> str:
    """将体检档案渲染为结构严谨、可直接汇报的 Markdown 报告。"""
    lines = [
        "# 📑 论文综合学术与数值体检报告 (FieldMate Comprehensive Diagnosis)",
        f"- **目标论文**：`{dossier.paper_id}` (`{dossier.paper_path}`)",
        f"- **检出潜在缺陷数**：{len(dossier.matched_defects)} 项",
        f"- **挖掘突破点候选**：{len(dossier.breakthroughs)} 项",
        "",
        "---",
        "",
        "## 一、 核心学术槽位提取 (Reading Card)",
    ]

    rc = dossier.reading_card
    for slot_name in ("protocol", "mechanism", "assumption", "gap", "claim"):
        slot_data = rc.get(slot_name, {})
        title = slot_data.get("title", slot_name.capitalize())
        candidates = slot_data.get("candidates", [])
        lines.append(f"### 1.{len(lines)} {title} ({slot_name})")
        if candidates:
            for c in candidates[:3]:
                lines.append(f"- > {c}")
        else:
            lines.append("- *(正文未抽取到显式候选句)*")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 二、 潜在数值陷阱与缺陷对拍 (Defect Pattern Audit)",
    ])
    if dossier.matched_defects:
        lines.append("| 缺陷 ID | 分类 | 严重度 | 缺陷描述 |")
        lines.append("|---|---|:---:|---|")
        for d in dossier.matched_defects:
            title_clean = d["title"][:40]
            lines.append(
                f"| **`{d['id']}`** | `{d['category']}` | `{d['severity']}` | {title_clean} |"
            )
        lines.append("")
    else:
        lines.append("✅ 未检测到显式的已知高危数值陷阱。\n")

    lines.extend([
        "---",
        "",
        "## 三、 突破点逆向推导与下一步建议 (Breakthrough Discovery)",
    ])
    if dossier.breakthroughs:
        lines.append(breakthrough_markdown(dossier.breakthroughs, paper_desc=dossier.paper_id))
    else:
        lines.append("未匹配到明确的经典突破原型，建议结合具体应用场景进一步细化。\n")

    if dossier.recommended_prereg:
        lines.extend([
            "---",
            "",
            "## 四、 自动推荐的可证伪实验方案 (Recommended Preregistration)",
            f"- **实验 ID**：`{dossier.recommended_prereg.get('id', '')}`",
            f"- **核心主张**：{dossier.recommended_prereg.get('claim', '')}",
            "",
            "| # | 假设语句 | 待测指标 | 认输条件 | 必需对照 |",
            "|---|---|---|---|---|",
        ])
        for h in dossier.recommended_prereg.get("hypotheses", []):
            reqs = ", ".join(h.get("support_required", [])) or "-"
            stmt = h.get("statement", "")
            metric = h.get("metric", "")
            falsif = h.get("falsification", "")[:45]
            lines.append(f"| {h.get('id')} | `{stmt}` | `{metric}` | {falsif} | `{reqs}` |")
        lines.append("")

    return "\n".join(lines)
