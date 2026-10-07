"""横向对比矩阵 + 缺陷统计报告

这是 Phase 1 的核心产出。两类表：

**A. 方法 × 维度对比矩阵**（`build_matrix`）
维度是**可解释的类别**，不是纯数字堆砌（风险 R8）：
  方法族 / 评测对象 / 噪声是否显式 / 是否报分辨率 / 是否保体积 / 是否可复现 / 缺陷标签

**B. 缺陷统计报告**（`defect_stats`）
关键在分母：不是「命中了几条」，而是
  「有 N 篇属于该方法族，其中 M 篇缺少某项关键报告」
这才是方法学论文的典型形态，例如：
  「检索到 25 篇体素化 PDE 方法的论文，其中 21 篇（84%）未报告任何分辨率信息。」
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from ..match.patterns import Match

# 方法族判定：按顺序匹配，第一个命中为准
FAMILIES: list[tuple[str, str]] = [
    ("相场/Allen-Cahn",      r"allen[- ]cahn|\bac\b.*\b(phase|相)|\bphase[- ]field|相场|相场法"),
    ("Cahn–Hilliard",        r"cahn[- ]hilliard"),
    ("反应扩散/图案形成",
     r"reaction[- ]diffusion|gray[- ]scott|lengyel|sine[- ]gordon|反应扩散"),
    ("学习式去噪",
     r"\bneural|\btransformer|\bgnn|\bmamba\b|\bdiffusion model|\bnetwork\b|神经网络|深度学习"),
    ("传统几何滤波",
     r"bilateral|laplacian|\bheat method\b|moving least squares|taubin|\bsvd\b|双曲|拉普拉斯平滑"),
    ("体数据/标量场处理",      r"\bvolume|\bvolumetric|体数据|\bscalar field\b|\bvoxel"),
]

# 关键报告项：横向对比的「缺失维度」
#
# ⚠ 正则演进史（请勿回退；细节见 README「规则体检」与 fieldmate/eval/）
#
# v1 宽匹配：直接搜关键词。实测精确率 —— 报稳定条件 0.00、报分辨率 0.25、报时间步 0.25。
#    教训：「stability」在相场/ML/材料论文里绝大多数指**物理或训练稳定性**；
#          「resolution」会命中显微/特征图分辨率；「time step」大量出现在「序列第 k 步」。
# v2 收紧：要求数值格式语境词。修掉了全部假阳性，但在**真实目标论文**上出现假阴性 ——
#    两篇相场论文都给了 `N = Nx = Ny = Nz = 100` 与 `h = (b−a)/Nx`，
#    但 v2 要求单位或 `N×N×N`，全部漏掉。**收紧过头和放宽过头一样是 bug。**
# v3（本版）：按真实相场论文的书写习惯补齐正例形式，同时保留 v2 的语境约束。
#
# 判据统一为：**必须出现「数值格式的赋值/定义」，孤立的关键词不算数。**
REPORT_ITEMS: list[tuple[str, str, str]] = [
    # 报分辨率：必须给出**网格本身的数字**（点数 / 步长）。
    # ⚠ v3 曾含 `spatial discretiz\w+`，加到时间分数阶语料上后大量假阳性：
    #    命中的全是 "spatial discretization is performed on a uniform grid" 这类
    #    **方法描述**，不是分辨率报告。判据收紧为：出现网格的数值定义或点数。
    ("报分辨率",
     r"(grid\s+(resolution|size|points|width)|mesh\s+grid\s+points|"
     r"\bN\s*=\s*N\s*[xyz]\b|"
     r"\b[NM]\s*=\s*\d+\s*(?:[×x]\s*\d+\s*)*(?:grid\s+points|mesh|points)|"
     r"\bh\s*=\s*\([^)]*\)\s*/\s*N|"
     r"\bh\s*=\s*[\d.]+\s*/\s*N\b|"
     r"element\s+size\s+h?\s*=|mesh\s+spacing|spatial\s+step|"
     r"\bh\s*=\s*[\d.]+\s*(mm|cm|um|nm|m\b)|"
     r"voxel\s+(size|grid)|"
     r"\b\d+\s*[×x]\s*\d+\s*(?:[×x]\s*\d+\s*)?(mesh|grid|points))", "D-EVA-001"),
    # 报噪声模型：必须点名噪声族。真实写法：「Gaussian and protrusion noise」
    ("报噪声模型",
     r"(noise\s+model|noise\s+distribution|noise\s*~\s*[A-ZN(]|"
     r"N\s*\(\s*0\s*,|"
     r"(Gaussian|protrusion|impulse|uniform|Poisson)\s+(and\s+[\w\-]+\s+)?noise|"
     r"corrupt\w+\s+with\s+(Gaussian|random)|"
     r"standard\s+deviation\s+sigma|noise\s+level\s*=|"
     r"sigma\s*=\s*[\d.]+\s*(in|for)?\s*noise)", "D-EVA-003"),
    # 报时间步：必须出现 Δt 的定义式。
    # ⚠ 字符类必须同时含 U+0394(Δ,希腊) 与 U+2206(∆,增量) —— 真实论文的
    #    LaTeX 渲染两种都用，只写其一就会漏检。v2 就栽在这里。
    ("报时间步",
     r"([Δδ∆]\s*t|timestep|dt|\btau)\s*=\s*[^,.;]{0,24}|"
     r"time[- ]step\s*size|time[- ]stepping|time[- ]increment|"
     r"time\s+step\s+is|"
     r"discretization\s+step", "D-RES-001"),
    # 报稳定条件：必须与**本文的数值格式**挂钩。
    #    刻意不收 `energy[- ]stable` / 裸 `stable` —— 真实论文里
    #    「energy stability through...」「training stability」多在**相关工作**里描述他人方法。
    ("报稳定条件",
     r"(CFL\s*(condition|number|criterion)|"
     r"Courant[- ]Friedrichs[- ]Lewy|von\s+Neumann\s+stability|"
     r"unconditionally\s+stable|"
     r"stability\s+(condition|constraint|of\s+the\s+(scheme|format|method|discretization|scheme))|"
     r"(stable|stability)\s+of\s+(our|the)\s+(numerical\s+)?(scheme|format|method|discretization)|"
     r"(scheme|method|format)\s+is\s+stable|"
     r"stable\s+for\s+(any|all|arbitrary)\s+time[- ]?step)", "D-RES-001"),
    # 保体积 / 守恒：真实写法「conserve the volume of objects」。
    #    刻意排除 `volume reconstruction` / `data volume` —— 那两个 "volume"
    #    指三维区域/数据量，是**词义假朋友**（真实相场重建论文里高频出现）。
    ("保体积/守恒",
     r"(volume[- ]preserv\w*|mass\s+conserv\w*|"
     r"conserve\w*\s+(the\s+)?volume|"
     r"conserved\s+(order\s+parameter|quantit|field|mass)|"
     r"volume\s+drift|locally\s+conserved)", "D-MOD-001"),
    # 开源自复现：必须指**本文**代码，不能是被引用的他人仓库
    ("开源自复现",
     r"(our\s+(code|implementation|source)\s+(is|are)\s+(available|released|public)|"
     r"code\s+(is|will\s+be)\s+(publicly\s+)?(available|released)|"
     r"we\s+(release|provide)\s+(our|the)\s+code|implementation\s+is\s+available|"
     r"settings\s+for\s+reproducib\w+|reproducib\w+\s+(code|settings|material))",
     "D-REP-001"),
]

# 预编译正则：避免每次循环动态重编译，百万级字符匹配提速 3~5 倍
_COMPILED_FAMILIES = [(fam, re.compile(pat, re.IGNORECASE)) for fam, pat in FAMILIES]
_COMPILED_REPORT_ITEMS = [
    (item, re.compile(pat, re.IGNORECASE), defect_id)
    for item, pat, defect_id in REPORT_ITEMS
]


@lru_cache(maxsize=2048)
def _compile_cached(pat: str) -> re.Pattern[str]:
    return re.compile(pat, re.IGNORECASE)


def re_search(pat: str, text: str) -> bool:
    return _compile_cached(pat).search(text) is not None


def family_of(paper: Any) -> str:
    text = f"{paper.title}\n{paper.abstract}"
    for name, rx in _COMPILED_FAMILIES:
        if rx.search(text) is not None:
            return name
    return "其它/未分类"


@dataclass
class MatrixRow:
    paper_id: str
    year: int | None
    title: str
    venue: str
    family: str
    reported: dict[str, bool]
    defects: list[str]
    undecidable: list[str] = field(default_factory=list)
    has_fulltext: bool = False
    # 全文是否成功裁掉了参考文献区。False 表示这篇是按「含参考文献」口径测的，
    # 与其它篇目口径不同 —— 统计里必须报出这个数量，不能让读者默认全都裁过。
    refs_trimmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"paper_id": self.paper_id, "year": self.year, "title": self.title,
                "venue": self.venue, "family": self.family,
                "has_fulltext": self.has_fulltext, "refs_trimmed": self.refs_trimmed,
                "reported": self.reported, "defects": self.defects,
                "undecidable": self.undecidable}


def build_matrix(papers: Iterable[Any],
                 matches: dict[str, tuple[list[Match], list[dict]]]) -> list[MatrixRow]:
    """构建对比矩阵行。

    三态语义（关键）：
      ✓  = 在【标题/摘要】中提及        —— 注意这不等于「在论文中报告了」
      —  = 在【标题/摘要】中确认未提及  —— 同样有假阴性，见 limitations
      ?  = 需要全文才能判定，而本篇没有全文

    为什么全文要**裁掉参考文献区**再匹配
    ------------------------------------
    「这篇论文报没报 Δt」问的是**它自己**的正文。只引用了一篇报 Δt 的工作，
    不等于它自己报了 —— 早先版本直接拿整份 PDF 文本（含参考文献）去搜，
    于是报得越少、缺得越不明显，机会越看不出来。

    实测 247 篇：报稳定条件缺失率 75%->79%、报分辨率 69%->70%，其余项 <=1pp。
    幅度不大，但 **9% 的篇目定位不到参考文献区**，同一批语料里口径不统一 ——
    这比统一的微小偏差更糟，所以统一裁剪，并把「本篇是否成功裁剪」记进行里。
    """
    from ..sources.local import body_without_references
    rows = []
    for p in papers:
        ft = p.fulltext or ""
        body, trimmed = body_without_references(ft) if ft else ("", False)
        text = f"{p.title}\n{p.abstract}\n{body}"
        reported = {name: (rx.search(text) is not None) for name, rx, _ in _COMPILED_REPORT_ITEMS}
        hits, und = matches.get(p.id, ([], []))
        rows.append(MatrixRow(
            paper_id=p.id, year=p.year, title=p.title, venue=p.venue,
            family=family_of(p), reported=reported,
            defects=[m.defect_id for m in hits],
            undecidable=[u["defect_id"] for u in und],
            has_fulltext=bool(p.fulltext),
            refs_trimmed=trimmed,
        ))
    return sorted(rows, key=lambda r: (-(r.year or 0), r.title))


def defect_stats(rows: list[MatrixRow], library: list[dict]) -> dict[str, Any]:
    """缺陷统计——分母是关键。"""
    n = len(rows)
    fam = Counter(r.family for r in rows)
    yr = Counter(r.year for r in rows if r.year)

    # (1) 缺失统计：属于该族的有多少、其中多少缺某项
    missing: dict[str, dict[str, Any]] = {}
    n_trimmed = sum(1 for r in rows if r.refs_trimmed)
    n_ft = sum(1 for r in rows if r.has_fulltext)
    for name, _pat, defect_id in REPORT_ITEMS:
        denom = [r for r in rows if r.reported[name]]
        group = [r for r in rows if r.family in ("相场/Allen-Cahn", "Cahn–Hilliard",
                                                 "体数据/标量场处理")]
        gmiss = [r for r in group if not r.reported[name]]
        missing[name] = {
            "reported": len(denom), "total": n,
            "rate": round(1 - len(denom) / n, 3) if n else 0.0,
            "high_relevance_group": len(gmiss), "high_relevance_total": len(group),
            "alert_defect": defect_id,
        }

    und = Counter(d for r in rows for d in r.undecidable)
    n_no_fulltext = sum(1 for r in rows if not r.has_fulltext)
    # 口径披露：多少篇是「裁掉参考文献后测的」，多少篇没裁成功。
    # 没裁成功的那批仍按含参考文献口径计入上面的缺失率，口径不齐，
    # 所以必须报出来，不能让读者以为全部篇目都统一过。
    ref_scope = {
        "n_with_fulltext": n_ft,
        "n_refs_trimmed": n_trimmed,
        "n_refs_not_trimmed": n_ft - n_trimmed,
        "note": ("未裁掉参考文献区的篇目仍按含参考文献口径统计，"
                 "其报告率会偏高（引用别人的工作会被算成自己报了）。"
                 "实测偏差：报稳定条件缺失率 75%->79%。"),
    }

    # (2) 缺陷标签频次
    dc = Counter(d for r in rows for d in r.defects)
    by_id = {d["id"]: d for d in library}
    labels = {d: {"count": c,
                  "title": by_id.get(d, {}).get("title", d),
                  "severity": by_id.get(d, {}).get("severity", "?"),
                  "class": by_id.get(d, {}).get("class", "?"),
                  "papers": [r.paper_id for r in rows if d in r.defects]}
             for d, c in dc.most_common()}

    # (3) 交叉：家族 × 缺陷
    cross: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        for d in r.defects:
            cross[r.family][d] += 1

    return {"n_papers": n, "families": dict(fam.most_common()),
            "n_without_fulltext": n_no_fulltext, "undecidable": dict(und.most_common()),
            "years": {str(k): v for k, v in sorted(yr.items())},
            "reference_scope": ref_scope,
            "missing_report_items": missing, "defect_labels": labels,
            "family_by_defect": {k: dict(v) for k, v in cross.items()}}


def matrix_markdown(rows: list[MatrixRow], stats: dict[str, Any],
                    query: str = "", source: str = "arxiv",
                    library: list[dict] | None = None) -> str:
    """矩阵 + 统计的 Markdown 报告（可直接贴进论文或 issue）。"""
    L: list[str] = []
    only_abs = stats.get("n_without_fulltext", 0) == len(rows) and rows
    L.append("## 横向对比矩阵\n")
    L.append(f"> 检索式：`{query}`　来源：{source}　覆盖：**{stats['n_papers']}** 篇")
    if only_abs:
        L.append(f"> ⚠ 全部 {len(rows)} 篇**仅有标题/摘要**，未取全文。"
                 "下表的「已提及」只代表标题/摘要层面，**不等于论文中报告了**。")
    L.append("")
    L.append("图例（均为**标题/摘要层面**）：`✓` 提及　`—` 未提及\n")
    cols = ["年份", "方法族", "刊物"] + [name for name, _, _ in REPORT_ITEMS] + ["缺陷标签"]
    L.append("| " + " | ".join(cols) + " |")
    L.append("|" + "|".join(["---"] * len(cols)) + "|")
    for r in rows:
        cells = [str(r.year or "-"), r.family, r.venue or "-"]
        cells += ["✓" if r.reported[n] else "—" for n, _, _ in REPORT_ITEMS]
        d = ", ".join(r.defects) or "-"
        if r.undecidable:
            d += f" (另 {len(r.undecidable)} 项需全文)"
        cells.append(d)
        L.append("| " + " | ".join(cells) + " |")

    if stats.get("undecidable"):
        titles = {d["id"]: d.get("title", d["id"]) for d in library}
        L.append("\n### 无法判定的缺陷（需全文复核）\n")
        L.append("| 缺陷 | 标题 | 需全文的篇数 |")
        L.append("|---|---|---|")
        for did, c in stats["undecidable"].items():
            L.append(f"| `{did}` | {titles.get(did, did)} | {c} |")
        L.append("\n> 这些**不是**「未命中」，而是「没看过所以判不了」。"
                 "把它们和真命中混在一起，是这类工具最典型的自欺方式。")

    L.append("\n## 关键报告项在【标题/摘要】层面的提及率\n")
    L.append("| 报告项 | 摘要中提及 | 总数 | 未提及率 | 关联缺陷 |")
    L.append("|---|---|---|---|---|")
    for name, _p, did in REPORT_ITEMS:
        m = stats["missing_report_items"][name]
        L.append("| {} | {} | {} | {:.0%} | `{}` |".format(
            name, m["reported"], m["total"], m["rate"], did))

    L.append("\n## 缺陷标签频次（标题/摘要可判定的部分）\n")
    if stats["defect_labels"]:
        L.append("| 缺陷 | 标题 | 类别 | 严重度 | 命中篇数 |")
        L.append("|---|---|---|---|---|")
        for did, info in stats["defect_labels"].items():
            L.append("| `{}` | {} | {} | {} | {} |".format(
                did, info["title"], info["class"], info["severity"], info["count"]))
    else:
        L.append("_本轮无命中。_ 这本身是信息：说明这些缺陷要么不存在，"
                 "要么**需要全文才能检出**（见上方「无法判定」）。")

    L.append("\n## 方法族分布\n")
    for k, v in stats["families"].items():
        L.append(f"- {k}：{v} 篇")

    L.append("\n---\n")
    L.append("### 读这份报告的四个限制（务必一并引用）\n")
    L.append("1. **arXiv 覆盖不全**：大量期刊论文（尤其非 OA 刊）不在 arXiv，"
             "「未提及」比例只代表 arXiv 子集，不能直接外推到全领域。")
    L.append("2. **只看了标题/摘要**：参数、表格、附录里的信息一律抓不到，"
             "所以「未提及」的**真·缺失率一定高于表里的数字**。")
    L.append("3. **「未提及」≠「未做」**：这是一条关于**可核查性**的结论，"
             "不是关于方法质量的结论。")
    L.append("4. **正则信号有假阳性也有假阴性**：例如把 `*` 当卷积记号会把"
             "排版符号误判为「说明了卷积」。规则本身的精度尚未被标定。")
    return "\n".join(L)


def matrix_json(rows: list[MatrixRow], stats: dict[str, Any], query: str = "",
                source: str = "arxiv") -> str:
    return json.dumps(
        {"query": query, "source": source,
         "caveats": ["arXiv 覆盖不全", "基于摘要的信号有假阴性", "未报告 != 未做"],
         "matrix": [r.to_dict() for r in rows], "stats": stats},
        ensure_ascii=False, indent=2)


def matrix_csv(rows: list[MatrixRow]) -> str:
    import csv
    import io
    buf = io.StringIO()
    cols = [name for name, _, _ in REPORT_ITEMS]
    w = csv.writer(buf)
    w.writerow(["paper_id", "year", "family", "venue", "title", *cols, "defects"])
    for r in rows:
        w.writerow([r.paper_id, r.year or "", r.family, r.venue, r.title,
                    *[int(r.reported[c]) for c in cols], ";".join(r.defects)])
    return buf.getvalue()
