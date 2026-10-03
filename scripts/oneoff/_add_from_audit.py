"""把 topics 审计指向的关键论文抓进语料，并复测审计。

为什么值得专门做
--------------
`fieldmate topics` 报「可微求解器 1 篇」，而那 1 篇还不是可微**相场**求解器
（是 shape optimization + adjoint + gradient-based 的流固耦合）。
按上一步调研，同方向最对口的公开实现是 JAX-PF（arXiv 2601.06079）——
它自带 AC / CH / 耦合 AC-CH 四个 benchmark，显式+隐式两套时间积分。

它不在 247 篇语料里（实测 `any('2601.06079' in id)` 为 False）。
**这就是审计的用处：指出一个具体的、能补的缺口**，而不是泛泛说"覆盖不足"。

流程：抓取 -> 入 manifest -> 标注它是「审计发现后补入」-> 复测 topics。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '.')
from fieldmate.sources.arxiv import Paper, RateLimiter                 # noqa: E402
from fieldmate.sources.fulltext import attach_fulltext, BULK_INTERVAL   # noqa: E402
from fieldmate.sources.local import parse_pdf_cached                   # noqa: E402

MF = Path("libraries/corpus_bulk.json")

# 审计指向的补入目标。每条都写明**为什么**它值得进语料 —— 不做无理由扩库。
WANTED = [
    ("2601.06079",
     "JAX-PF：JAX-FEM 上的可微相场，含 AC/CH/耦合四个 benchmark、显式+隐式两套"
     "时间积分、Eshelby 包含。topics 审计报「可微求解器 1 篇」且那篇不是可微相场，"
     "这是同方向最对口的公开实现。"),
    ("2604.23920",
     "Survey on topological methods for Allen--Cahn equations and systems —— "
     "AC 方程的拓扑方法综述，导师主方程侧的方法地图。"),
    ("2405.05098",
     "Energy stable gradient flow schemes for shape and topology optimization —— "
     "形状/拓扑优化的能量稳定梯度流格式，与导师「Cahn-Hilliard + 能量稳定性」那条线同源。"),
]


def main() -> int:
    mf = json.loads(MF.read_text(encoding="utf-8"))
    have = {p["id"].split("v")[0] for p in mf["papers"]}

    to_get: list[Paper] = []
    for aid, why in WANTED:
        if aid in have:
            print(f"[skip] {aid} 已在语料中")
            continue
        to_get.append(Paper(id=aid, title=why[:0] or f"(待取元数据 {aid})", abstract=""))
        print(f"[want] {aid}  {why[:60]}...")

    if not to_get:
        print("无需补入。")
        return 0

    print(f"\n抓取 {len(to_get)} 篇（间隔 {BULK_INTERVAL}s）…")
    st = attach_fulltext(to_get, verbose=True, limiter=RateLimiter(BULK_INTERVAL))
    print(f"下载={st['downloaded']} 解析成功={st['parsed']} 未取得={st['throttled']}")

    added = 0
    for p in to_get:
        if not p.fulltext:
            print(f"[miss] {p.id} 没拿到，跳过（不写进 manifest）")
            continue
        aid = p.id
        why = next((w for a, w in WANTED if a == aid), "")
        mf["papers"].append({
            "id": aid,
            "title": p.title,
            "published": p.published,
            "categories": p.categories,
            "has_fulltext": True,
            "n_chars": len(p.fulltext),
            "added_by": "topics-audit-gap",
            "added_reason": why,
        })
        added += 1
        print(f"[add] {aid}  {len(p.fulltext):,} 字符")

    mf["n_papers"] = len(mf["papers"])
    mf["n_with_fulltext"] = sum(1 for e in mf["papers"] if e.get("has_fulltext"))
    got_ids = {p.id.split("v")[0] for p in to_get if p.fulltext}
    mf.setdefault("provenance", []).extend(
        {"id": a, "reason": w, "added": time.strftime("%Y-%m-%d")}
        for a, w in WANTED if a in got_ids)
    MF.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n清单：{mf['n_papers']} 篇，有全文 {mf['n_with_fulltext']}（本次新增 {added}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
