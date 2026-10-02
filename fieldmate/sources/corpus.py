"""自动语料构建（harvest）—— 从检索式到可用语料，中间必须过相关性闸门

为什么不能「抓到就存」
----------------------
2026-10-02 实测：`all:"phase field" AND all:denoising` 抓 7 篇，**0 篇**相关。
不加闸门的话，语料里一半是扩散模型论文，gold set 和规则评估会一起被带偏。
所以 harvest 的价值不在「抓」，在**「抓完敢扔」**。

两道闸门（都确定性、可解释、可复现）
------------------------------------
    闸门 A 学科门：arXiv 主分类必须落在计算数学/物理材料/成像的范围内。
           cs.LG / cs.CV / cs.AI 这类 ML 主分类直接判死 —— 除非它同时挂了数学分类
           （那种情况多半是「用 PDE 做机器学习」，反而值得留）。
    闸门 B 词法门：必须出现本方向的核心术语（取自导师论文的真实关键词）。

**被拒的也要记账。** 每次 harvest 写出一份 rejection log：谁被拒、因为哪道门。
这比只记「收了 12 篇」有用得多——它让你随时检查闸门是不是把好东西挡在门外了。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .arxiv import Paper, RateLimiter, collect
from .fulltext import attach_fulltext, corpus_fingerprint, default_cache

__all__ = ["DomainFilter", "HarvestResult", "harvest", "harvest_markdown",
           "CORE_CATEGORIES", "ML_CATEGORIES", "STRONG_TERMS", "WEAK_TERMS"]

# ---- 闸门 A：分类白/黑名单 ----
# 计算数学 + 计算物理 + 成像：本方向的主场
CORE_CATEGORIES = {
    "math.NA", "math.AP", "math.DS", "math.PR", "math.SP",
    "math-ph", "cond-mat.mtrl-sci", "cond-mat.stat-mech",
    "cs.CE", "cs.NA", "cs.CV", "eess.IV",
}
# 纯 ML/生成模型方向：出现即拒（除非同时有数学分类，那属于交叉，值得留）
ML_CATEGORIES = {"cs.LG", "cs.AI", "cs.CL"}


def _read_categories(p: Paper) -> set[str]:
    return {c.split(".")[0] + "." + c.split(".")[1] if "." in c else c
            for c in p.categories}


# ---- 闸门 B：术语强弱分档 ----
#
# ⚠ 首版把 `phase[- ]field` 当强术语，实测在污染语料上放进 4 篇全不相关的：
#    · Data-efficient continuous conditional denoising diffusion model（ML 扩散模型）
#    · Energy Dissipative Solution to Nonlinear Parabolic Systems（PDE 理论，与去噪无关）
#    · Deep Learning Assisted Denoising of Experimental Micrographs（深度学习去噪）
#    · Optical Fringe Patterns Filtering（CNN 图像滤波）
# 根因：**「phase field」在 ML/物理论文里也指「系统的相场（序参量场）」**，
# 与「相场方法」完全是两回事。强术语必须是**有方法论排他性**的写法。
STRONG_TERMS = [
    r"phase[- ]field\s+(?:method|model|modeling|simulation|approximation|"
    r"reconstruction|technique|framework|equation|representation|discretiz\w*)",
    r"phase[- ]field\s+crystal", r"phase[- ]field\s+approximation",
    r"Allen[- ]Cahn", r"Cahn[- ]Hilliard", r"Ginzburg[- ]Landau",
    r"normalized\s+Langevin", r"Langevin\s+equation", r"Ornstein[- ]Uhlenbeck",
    r"Gray[- ]Scott", r"Lengyel[- ]Epstein", r"Sine[- ]Gordon", r"damping[- ]limit",
    r"time[- ]fractional", r"Caputo", r"Gr[oö]unwald", r"Mittag[- ]Leffler",
    r"marching\s+cubes", r"level[- ]set\s+(?:method|equation|evolution)",
    r"moving\s+by\s+mean\s+curvature", r"volume\s+reconstruction",
    r"mesh\s+denois\w*", r"mesh\s+smoothing", r"surface\s+reconstruction",
]
# 弱术语：只有搭配**非 ML 语境 + 核心数学分类**时才放行
WEAK_TERMS = [
    r"phase[- ]field", r"reaction[- ]diffusion", r"Turing\s+(?:pattern|instability)",
    r"point\s+cloud", r"level[- ]set", r"normaliz\w*\s+dynamics",
]

# 反向词：出现即说明这篇大概率是 ML 论文
_ML_FLAGS = [r"diffusion\s+model", r"denoising\s+diffusion", r"score[- ]based",
             r"neural\s+operator", r"physics[- ]informed", r"\bPINN",
             r"transformer", r"foundation\s+model", r"latent\s+diffusion",
             r"convolutional", r"\bCNN\b", r"deep\s+learning", r"neural\s+network",
             r"score\s+function", r"\bUNet\b", r"latent\s+space"]


@dataclass
class DomainFilter:
    """两道闸门 + 术语强弱分档。返回 (是否通过, 理由)。"""
    require_term: bool = True
    min_strong: int = 1
    allow_weak: bool = True
    ml_penalty: bool = True

    def check(self, paper: Paper) -> tuple[bool, str]:
        cats = _read_categories(paper)
        low = f"{paper.title} {paper.abstract}".lower()
        strong = [t for t in STRONG_TERMS if re.search(t, low)]
        weak = [t for t in WEAK_TERMS if re.search(t, low)]
        ml = [f for f in _ML_FLAGS if re.search(f, low)]
        has_core = bool(cats & CORE_CATEGORIES)
        ml_only = bool(cats & ML_CATEGORIES) and not has_core

        # 闸门 A：学科
        if ml_only:
            return False, (f"分类闸门：ML 主分类 {sorted(cats & ML_CATEGORIES)} "
                           f"且无数学/物理分类")
        if self.require_term and not has_core and not strong:
            return False, f"分类闸门：{sorted(cats)} 不在核心学科范围，且无强术语"

        # 闸门 B：术语强弱
        if self.require_term:
            if len(strong) >= self.min_strong:
                why = f"通过（强术语 {strong[:2]}，分类 {sorted(cats & CORE_CATEGORIES)[:2] or sorted(cats)[:2]}"
                if ml:
                    why += f"；⚠ 命中 ML 词 {ml[:2]}，但有强术语+核心分类，保留待人审"
                return True, why
            if self.allow_weak and weak and has_core and not ml:
                return True, f"通过（仅弱术语 {weak[:2]}，但分类 {sorted(cats & CORE_CATEGORIES)[:2]} 且无 ML 特征）"
            if weak and ml:
                return False, f"词法闸门：仅弱术语 {weak[:1]} 且命中 ML 词 {ml[:2]}"
            if weak:
                return False, f"词法闸门：仅弱术语 {weak[:1]}，无强术语支撑"
            return False, "词法闸门：未命中任何核心术语"
        return True, "通过（已关闭词法闸门）"


@dataclass
class HarvestResult:
    query: str
    label: str
    fetched: int = 0
    accepted: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_queries(path: str | Path | None, only: str | None = None) -> list[dict]:
    """读取检索式清单。

    only 语义：
        'high'（默认）只用验证过的高纯度检索式
        'all'  含被污染的检索式 —— 用来**验证闸门是否真能拦住污染**
        其他值  按 purity 字段精确匹配
    """
    p = Path(path) if path else Path(__file__).resolve().parents[2] / "libraries" / "queries.json"
    cfg = json.loads(p.read_text(encoding="utf-8"))
    qs = list(cfg["queries"]) + list(cfg.get("poisoned_queries", []))
    if not only or only == "all":
        return qs
    return [q for q in qs if q.get("purity") == only]


def harvest(out: str | Path | None = None, per_query: int = 10,
            only: str = "high", require_term: bool = True,
            do_download: bool = True, interval: float = 3.0,
            queries_path: str | Path | None = None,
            cache: str | Path | None = None, verbose: bool = True) -> dict[str, Any]:
    """按检索式自动抓取 + 过闸门 + 下载全文 + 写出带出处的语料清单。"""
    qs = _load_queries(queries_path, only)
    if not qs:
        raise ValueError("没有匹配的检索式；only 可取 'high' 或 'all'")

    limiter = RateLimiter(interval)
    flt = DomainFilter(require_term=require_term)
    cache_dir = Path(cache) if cache else default_cache()

    seen: set[str] = set()
    results: list[HarvestResult] = []
    papers: list[Paper] = []

    for q in qs:
        r = HarvestResult(query=q["query"], label=q.get("label", ""))
        try:
            got = collect(q["query"], target=per_query, limiter=limiter)
        except Exception as e:                                   # noqa: BLE001
            if verbose:
                print(f"[skip] {q['query']} -> {e}")
            r.rejected.append({"id": "-", "title": "-", "reason": f"检索失败：{e}"})
            results.append(r)
            continue
        for p in got:
            base = p.id.split("v")[0]
            r.fetched += 1
            if base in seen:
                continue
            seen.add(base)
            ok, why = flt.check(p)
            if ok:
                papers.append(p)
                r.accepted.append({"id": p.id, "title": p.title[:110],
                                  "reason": why, "via_query": q["query"]})
            else:
                r.rejected.append({"id": p.id, "title": p.title[:110], "reason": why})
        results.append(r)
        if verbose:
            print(f"[{q.get('label', '')[:34]:<34}] 抓取 {r.fetched:>3} "
                  f"→ 收 {len(r.accepted):>3} / 拒 {len(r.rejected):>3}")

    stat = {"downloaded": 0, "cached": 0, "parsed": 0, "failed": 0}
    if do_download and papers:
        if verbose:
            print(f"\n下载 {len(papers)} 篇全文（缓存 {cache_dir}）…")
        st = attach_fulltext(papers, cache=cache_dir, limiter=limiter, verbose=False)
        stat = {k: st[k] for k in stat}
        if verbose:
            print(f"  下载={st['downloaded']} 缓存命中={st['cached']} "
                  f"解析成功={st['parsed']} 失败={st['failed']}")

    manifest = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "fingerprint": corpus_fingerprint(papers) if papers else "-",
        "gate": {"require_term": require_term,
                 "core_categories": sorted(CORE_CATEGORIES),
                 "ml_categories": sorted(ML_CATEGORIES),
                 "n_strong_terms": len(STRONG_TERMS), "n_weak_terms": len(WEAK_TERMS)},
        "counts": {"queries": len(results),
                   "fetched": sum(r.fetched for r in results),
                   "accepted": sum(len(r.accepted) for r in results),
                   "rejected": sum(len(r.rejected) for r in results),
                   "with_fulltext": sum(1 for p in papers if p.fulltext),
                   **stat},
        "papers": [{"id": p.id, "title": p.title, "published": p.published,
                    "categories": p.categories, "has_fulltext": bool(p.fulltext)}
                   for p in papers],
        "per_query": [r.to_dict() for r in results],
    }

    out_p = Path(out) if out else Path("libraries") / "corpus_harvest.json"
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if verbose:
        c = manifest["counts"]
        print(f"\n合计：抓取 {c['fetched']} → 收 {c['accepted']} / 拒 {c['rejected']}"
              f"（拒收率 {c['rejected']/max(c['fetched'],1):.0%}）")
        print(f"全文可解析 {c['with_fulltext']} 篇　清单写入 {out_p}")
    return manifest


def harvest_markdown(m: dict[str, Any]) -> str:
    L = ["## 自动语料构建报告\n"]
    c = m["counts"]
    L.append(f"> 生成于 {m['generated']}　语料指纹 `{m['fingerprint']}`\n")
    L.append(f"抓取 **{c['fetched']}** → 收录 **{c['accepted']}** / "
             f"拒收 **{c['rejected']}**（拒收率 {c['rejected']/max(c['fetched'],1):.0%}）　"
             f"全文可解析 **{c['with_fulltext']}**\n")
    L.append("### 闸门配置\n")
    L.append(f"- 学科门核心分类：{', '.join(m['gate']['core_categories'])}")
    L.append(f"- 直接拒收的 ML 分类：{', '.join(m['gate']['ml_categories'])}")
    L.append(f"- 词法门：强术语 {m['gate']['n_strong_terms']} 条 + 弱术语 {m['gate']['n_weak_terms']} 条　"
             f"是否强制命中：{'是' if m['gate']['require_term'] else '否'}\n")
    L.append("### 逐检索式\n")
    L.append("| 检索式 | 抓取 | 收录 | 拒收 |")
    L.append("|---|---|---|---|")
    for r in m["per_query"]:
        L.append("| {} | {} | {} | {} |".format(
            r["query"][:56], r["fetched"], len(r["accepted"]), len(r["rejected"])))
    rej = [(r, x) for r in m["per_query"] for x in r["rejected"]]
    if rej:
        L.append("\n### 拒收样例（检查闸门有没有误杀）\n")
        for r, x in rej[:15]:
            L.append(f"- `{x['id']}` {x['title'][:62]}\n  - 理由：{x['reason']}")
    return "\n".join(L)
