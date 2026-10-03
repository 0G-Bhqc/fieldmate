"""缺陷库加载 + 规则匹配（纯确定性，无 LLM）

这是框架的核心判定环节（§1 原则：判定归脚本）。

匹配逻辑分三步：
  1. **前置条件** applies_if：论文必须先属于某方法族，才谈得上该族的缺陷
     （例如「用 Cahn–Hilliard 但没提 SAV」对一篇做图像分割的论文无意义）。
  2. **信号求值**：present / absent / regex 三类信号在指定 scope 上求值。
     `absent` 是横向对比最有力的信号——**信息缺失**比信息错误更难被自查发现。
  3. **告警判定**：默认所有 signals 都满足才算命中；`composite: both` 显式要求全部存在。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

# 可疑度：absent 类信号比 present 类更容易造成"结论反向"，权重更高
WEIGHTS = {"absent": 1.0, "present": 0.4, "regex": 0.4}


def _default_library() -> Path:
    from .._paths import library_dir
    return library_dir()


def _default_rules() -> Path:
    from .._paths import contracts_dir
    return contracts_dir() / "detection_rules.json"


@dataclass
class Match:
    defect_id: str
    title: str
    severity: str
    score: float
    evidence: list[dict[str, Any]]
    status: str

    def to_dict(self) -> dict[str, Any]:
        return {"defect_id": self.defect_id, "title": self.title,
                "severity": self.severity, "score": round(self.score, 3),
                "evidence": self.evidence, "status": self.status}


def load_library(path: str | Path | None = None) -> list[dict[str, Any]]:
    p = Path(path) if path else _default_library() / "defect_patterns.jsonl"
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


@lru_cache(maxsize=8)
def _rules_cached(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_rules(path: str | Path | None = None) -> dict[str, Any]:
    p = str(path) if path else str(_default_rules())
    return _rules_cached(p)


def _compile(pat: str) -> re.Pattern[str]:
    return re.compile(pat, re.IGNORECASE)


def _signal_hits(sig: dict[str, Any], texts: dict[str, str]) -> tuple[bool, str]:
    """求值单个信号。返回 (是否满足该信号类型的要求, 证据说明)。"""
    scope = sig.get("scope", "any")
    text = texts.get(scope) or texts.get("any") or ""
    rx = _compile(sig["pattern"])
    found = rx.search(text) is not None
    kind = sig["type"]
    if kind == "absent":
        return (not found), ("" if not found else f"意外出现：{sig['pattern'][:48]}")
    return found, (f"命中：{rx.search(text).group(0)[:40]}" if found else f"未出现：{sig['pattern'][:40]}")


def match_paper(paper: Any, library: list[dict[str, Any]],
                rules: dict[str, Any]) -> tuple[list[Match], list[dict[str, Any]]]:
    """对单篇论文跑全部检测规则。

    返回 (命中列表, 无法判定列表)。

    ⚠ 「无法判定」这一路是**必须有的**，不是可选的。
    早期版本对 fulltext 为空的论文（arXiv 只有摘要时就是如此），
    仍会把 fulltext 作用域的 `absent` 信号判为「满足」，
    于是 D-REP-001（静默兜底，一个纯代码缺陷）在 7/7 篇上全部假阳性命中。

    正确语义是：**「未出现」只有在「确实看过」时才是信号。**
    没看过 = 无法判定，不能记为「已确认缺失」。
    这条本身就是缺陷库 D-REP-001 教训的直接应用。
    """
    texts = {s: paper.text_for(s) for s in ("title", "abstract", "fulltext", "any")}
    has_fulltext = bool(texts.get("fulltext"))
    by_id = {d["id"]: d for d in library}
    out: list[Match] = []
    undecidable: list[dict[str, Any]] = []

    for rule in rules.get("rules", []):
        defs = by_id.get(rule["id"])
        if defs is None:
            continue                                   # 规则库里引用了未收录的缺陷
        # 0) 依赖全文的规则，若没有全文则标记为「无法判定」而非「未命中」
        needs_fulltext = any(s.get("scope") == "fulltext" for s in rule.get("signals", []))
        if needs_fulltext and not has_fulltext:
            undecidable.append({"defect_id": rule["id"], "title": defs["title"],
                                "reason": "需要全文，本篇只有标题/摘要"})
            continue
        # 1) 前置条件：任一 present 条件组不满足 → 跳过
        groups = rule.get("applies_if", {}).get("present", [])
        ok_scope = True
        for scope, pat in groups:
            if not _compile(pat).search(texts.get(scope) or texts.get("any") or ""):
                ok_scope = False
                break
        if not ok_scope:
            continue
        # 2) signals 求值
        sigs = rule.get("signals", [])
        if not sigs:
            continue
        composite = rule.get("composite", "all")
        ev, score, satisfied = [], 0.0, True
        for s in sigs:
            ok, note = _signal_hits(s, texts)
            ev.append({"signal": s["type"], "scope": s.get("scope", "any"),
                       "pattern": s["pattern"], "satisfied": ok, "note": note,
                       "why": s.get("note", "")})
            score += WEIGHTS.get(s["type"], 0.4)
            if not ok:
                satisfied = False
        hit = all(e["satisfied"] for e in ev) if composite == "both" else satisfied
        if hit:
            out.append(Match(defect_id=rule["id"], title=defs["title"],
                             severity=defs.get("severity", "medium"),
                             score=score, evidence=ev,
                             status=defs.get("status", "reported")))
    return sorted(out, key=lambda m: (-m.score, m.defect_id)), undecidable


def match_all(papers, library=None, rules=None):
    """返回 {paper_id: (hits, undecidable)}。"""
    library = library if library is not None else load_library()
    rules = rules if rules is not None else load_rules()
    out = {}
    for p in papers:
        hits, und = match_paper(p, library, rules)
        out[p.id] = (hits, und)
    return out

