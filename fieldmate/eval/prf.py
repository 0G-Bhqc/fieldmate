"""检测规则的精确率/召回率评估 —— 工具对自己的诚实体检

存在的理由
----------
README 里写过一句「所有检测规则的精确率/召回率目前是未知的，
这是本工具最该被质疑的地方」。这个模块就是来还这笔债的。

没有它，「7/7 篇未报时间步」这类结论无法区分两种可能：
    (a) 这个领域真的集体不报      —— 立项机会
    (b) 我的正则把「训练稳定性」当成了「数值稳定性」 —— 工具在骗人
2026-10-02 的首轮实测给出了扎心的答案：**是 (b)**。

gold set 的来源与局限
---------------------
`libraries/goldset.jsonl` 由人工**读证据句**后逐条标注（每条带 evidence 字段说明理由）。
这是**人工判断，不是自动标注**——正则判不了「stability 指的是数值稳定性还是相变稳定性」。

局限必须说清：
  * N=30（5 篇 × 6 项），**每个 item 只有 5 个样本**，置信区间很宽。
  * 语料来自单一检索式，且该检索式本身已被判为跑偏（见 README 已知局限）。
  * 标注者只有我一个，存在系统性偏见的风险。
  * 因此本模块的定位是**发现明显失效的规则**，不是给出精确的度量。
    样本量要到每项 ≥30 条才谈得上「测量」。
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .._paths import data_dir as _data
from typing import Any

from ..compare.matrix import REPORT_ITEMS, re_search
from ..sources.arxiv import Paper
from ..sources.local import parse_pdf_cached

_CACHE: dict[str, str] = {}


def load_goldset(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path else _data("libraries") / "goldset.jsonl"
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def load_corpus(path: str | Path | None = None) -> dict[str, Any]:
    """语料清单：gold set 的 paper 标识 -> 本地文件路径。

    为什么必须有它：arXiv 检索对**计算数学方向基本无效**。2026-10-02 实测
    `all:"phase field" AND all:denoising` 取回 7 篇，**0 篇**真在做相场去噪 ——
    因为 "denoising" 在现代 ML 里专指「去噪扩散」，会把整个扩散模型文献拉进来。
    这个方向的论文普遍走期刊投稿、不上 arXiv，目标语料只能人工指定。
    """
    p = Path(path) if path else _data("libraries") / "corpus.json"
    if not p.exists():
        return {"cache_dir": ".fieldmate-cache/arxiv_pdfs", "papers": {}, "arxiv_papers": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _load_texts(pdf_dir: str | Path | None = None,
                corpus: dict[str, Any] | None = None) -> dict[str, str]:
    """把 gold set 涉及的论文全文读进来：arXiv 缓存 + corpus.json 指定的目标论文。"""
    corpus = corpus if corpus is not None else load_corpus()
    out: dict[str, str] = {}
    for pid, path in (corpus.get("papers") or {}).items():
        f = Path(path)
        if f.exists() and pid not in _CACHE:
            t, _ = parse_pdf_cached(f)
            _CACHE[pid] = t or ""
        if pid in _CACHE:
            out[pid] = _CACHE[pid]
    cache = Path(pdf_dir) if pdf_dir else Path(corpus.get("cache_dir", ".fieldmate-cache/arxiv_pdfs"))
    if cache.is_dir():
        for f in sorted(cache.glob("*.pdf")):
            if f.stem in _CACHE:
                out[f.stem] = _CACHE[f.stem]
                continue
            t, _ = parse_pdf_cached(f)
            _CACHE[f.stem] = t or ""
            out[f.stem] = _CACHE[f.stem]
    return out


@dataclass
class ItemPRF:
    item: str
    tp: int
    fp: int
    fn: int
    tn: int
    n_target: int = 0     # 目标领域论文的样本数（高置信度标注）
    n_other: int = 0      # 其他论文的样本数

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    @property
    def verdict(self) -> str:
        if self.tp + self.fp == 0:
            return "无用（无正例）"
        if self.precision < 0.5:
            return "❌ 不可用（精确率过低）"
        if self.precision < 0.8:
            return "⚠ 需收紧正则"
        if self.tp + self.fn == 0:
            return "⚠ 无负样本，召回率未知"
        return "✅ 可用"

    def to_dict(self) -> dict[str, Any]:
        return {"item": self.item, "tp": self.tp, "fp": self.fp, "fn": self.fn,
                "tn": self.tn, "precision": round(self.precision, 3),
                "recall": round(self.recall, 3), "f1": round(self.f1, 3),
                "verdict": self.verdict, "n_target": self.n_target,
                "n_other": self.n_other,
                "n_gold": self.tp + self.fp + self.fn + self.tn}


def evaluate_items(gold: list[dict], texts: dict[str, str]) -> list[ItemPRF]:
    pats = {name: pat for name, pat, _ in REPORT_ITEMS}
    acc: dict[str, dict[str, int]] = {
        n: dict(tp=0, fp=0, fn=0, tn=0, n_target=0, n_other=0) for n in pats}
    for g in gold:
        item = g["item"]
        if item not in pats:
            continue
        text = texts.get(g["paper"], "")
        pred = 1 if (text and re_search(pats[item], text)) else 0
        truth = int(g["label"])
        a = acc[item]
        a["n_target" if g.get("corpus") == "target" else "n_other"] += 1
        if pred and truth:
            a["tp"] += 1
        elif pred and not truth:
            a["fp"] += 1
        elif not pred and truth:
            a["fn"] += 1
        else:
            a["tn"] += 1
    return [ItemPRF(item=n, **c) for n, c in acc.items()]


def evaluate_family_classifier(samples: list[tuple[str, str, str]]) -> dict[str, Any]:
    """评估方法族分类器（samples: (paper_id, title+abstract, 真值族)）。

    2026-10-02 实测发现：检索式 `phase field AND denoising` 取回的 7 篇里，
    只有极少数真的在做相场去噪，其余是因为摘要里出现 "phase field" 字样
    被 `family_of()` 的正则收编。**语料跑偏会让后面所有统计失去意义**，
    所以这一项必须单独评估。
    """
    from ..compare.matrix import FAMILIES
    tp = fp = fn = 0
    misses = []
    for pid, text, truth in samples:
        low = text.lower()
        pred = "其它/未分类"
        for name, pat in FAMILIES:
            if re_search(pat, low):
                pred = name
                break
        if pred == truth:
            tp += 1
        else:
            if pred != "其它/未分类":
                fp += 1
            if truth != "其它/未分类":
                fn += 1
            misses.append({"paper": pid, "pred": pred, "truth": truth,
                           "head": text[:110]})
    n = tp + fp + fn
    return {"n": n, "tp": tp, "fp": fp, "fn": fn,
            "accuracy": round(tp / n, 3) if n else 0.0, "misses": misses}


def family_of(paper) -> str:
    from ..compare.matrix import family_of as _f
    return _f(paper)


def prf_markdown(prf: list[ItemPRF], n_papers: int, notes: list[str] | None = None) -> str:
    L = ["## 检测规则体检（gold set 实测）\n"]
    n_t = sum(r.n_target for r in prf)
    n_o = sum(r.n_other for r in prf)
    L.append(f"> 标注样本 **{n_papers}** 条 = 目标领域论文 {n_t} 条（高置信度，作者亲读全文）"
             f" + 其他论文 {n_o} 条。**每项样本量很小，置信区间宽。**\n")
    L.append("| 报告项 | TP | FP | FN | TN | 精确率 | 召回率 | F1 | 目标域样本 | 判定 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(prf, key=lambda x: x.precision):
        L.append("| {} | {} | {} | {} | {} | {:.2f} | {:.2f} | {:.2f} | {} | {} |".format(
            r.item, r.tp, r.fp, r.fn, r.tn, r.precision, r.recall, r.f1,
            r.n_target, r.verdict))
    if notes:
        L.append("\n### 失效案例（务必写进规则注释）\n")
        for n in notes:
            L.append(f"- {n}")
    L.append("\n### 这份体检本身的局限\n")
    L.append("1. **样本量极小**：每项 5~7 个样本，精确率的一格变动就是 14~20%。"
             "结论只能用于**发现明显失效**，不能当度量。")
    L.append("2. **标注者单一**：只有我一个人判读，存在系统性偏见。")
    L.append("3. **arXiv 语料已判跑偏**：`phase field AND denoising` 取回的 7 篇里"
             "**0 篇**真在做相场去噪（因为 denoising 一词在现代 ML 里专指去噪扩散，"
             "把整个扩散模型文献都拉了进来）。**计算数学方向普遍不上 arXiv**，"
             "目标语料只能靠 `libraries/corpus.json` 人工指定。")
    L.append("4. **假阴性比假阳性更难发现**：v2 规则在 arXiv 语料上精确率漂亮，"
             "却在这两篇真实相场论文上全部漏报（N=Nx=Ny=Nz 写法不匹配）。"
             "**语料错了，分数再高也没用。**")
    return "\n".join(L)


def prf_json(prf: list[ItemPRF], n_gold: int) -> str:
    return json.dumps({
        "n_gold": n_gold,
        "caveats": ["样本量极小，仅用于发现明显失效",
                    "标注者单一", "语料本身已判跑偏"],
        "items": [r.to_dict() for r in prf],
    }, ensure_ascii=False, indent=2)
