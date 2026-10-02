"""在真实语料上量五槽抽取的命中率 —— 找"最弱一环"的证据。

为什么必须实测而不是看代码
--------------------------
早先的记录是「read 的 Assumption 槽在真实论文上均未命中，是全包最弱一环」。
但这句话有两种完全相反的解释：

  A. 语料太少 / 只有摘要，正则没机会命中       -> 是**数据**问题，加全文即可
  B. ASSUMPTION_MARKERS 这套词法在真实论文里  -> 是**方法**问题，
     根本抓不到学者真正写假设的方式                换语料也没用

两者的处置完全相反，所以在补齐语料之后必须重新量一次。
实测数据：247 篇全文，1631 万字符。
"""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, '.')
from fieldmate.sources.fulltext import load_manifest          # noqa: E402
from fieldmate.extract.slots import extract_slots            # noqa: E402

SLOTS = ("Claim", "Mechanism", "Evidence", "Assumption", "Protocol", "Gap")


def main() -> int:
    papers, stats = load_manifest("libraries/corpus_bulk.json", verbose=False)
    print(f"语料：{stats['n_loaded']} 条，有全文 {stats['n_fulltext']}\n")

    hit = Counter()            # 槽 -> 有命中的论文数
    n_cand = Counter()         # 槽 -> 候选句总数
    zero = {s: [] for s in SLOTS}   # 槽 -> 一个候选都没抽到的论文
    section_fail = 0
    sect_count = Counter()          # 章节 -> 有该章节的论文数
    no_method = []                 # 缺 method 章节的论文
    examples: dict[str, list] = {s: [] for s in SLOTS}

    for p in papers:
        if not p.fulltext:
            continue
        try:
            res = extract_slots(p, p.fulltext, per_slot=99)
        except Exception as e:                       # noqa: BLE001
            print(f"  [{p.id}] 抽取抛异常 {type(e).__name__}: {e}")
            continue
        if not res.sections:
            section_fail += 1
        for sec in res.sections:
            sect_count[sec] += 1
        if "method" not in res.sections:
            no_method.append(p.id)
        for s in SLOTS:
            slot = res.slots.get(s)
            got = slot.items if slot else []
            if got:
                hit[s] += 1
                n_cand[s] += len(got)
                if len(examples[s]) < 2:
                    first = got[0]
                    txt = first.get("sentence", "") if isinstance(first, dict) else str(first)
                    examples[s].append((p.id, txt[:150]))
            else:
                zero[s].append(p.id)

    n = stats['n_fulltext']
    print(f"{'槽位':<12}{'命中论文':>10}{'命中率':>9}{'候选句':>10}{'零命中篇数':>12}")
    print("-" * 55)
    for s in SLOTS:
        rate = hit[s] / n if n else 0
        print(f"{s:<12}{hit[s]:>10}{rate:>8.1%}{n_cand[s]:>10}{len(zero[s]):>12}")

    print(f"\n章节切分失败（sections 为空）：{section_fail} / {n} "
          f"({section_fail/n:.1%})" if n else "")

    weakest = min(SLOTS, key=lambda s: hit[s])
    print(f"\n最弱槽位：{weakest}（命中率 {hit[weakest]/n:.1%}）")
    print(f"  它一个候选都没抽到的论文数：{len(zero[weakest])}")
    for pid in zero[weakest][:8]:
        print(f"    - {pid}")

    print("\n章节检出率：")
    for sec, c in sect_count.most_common():
        print(f"  {sec:<12}{c:>5} / {n}  ({c/n:.0%})")
    print(f"\n  缺 method 章节的论文：{len(no_method)} / {n} ({len(no_method)/n:.0%})")
    print("  Mechanism 只认 method 章节（SECTION_MAP），所以两者应当对上：")
    print(f"    缺 method = {len(no_method)}，Mechanism 零命中 = {len(zero['Mechanism'])}")

    print("\n各槽样例：")
    for s in SLOTS:
        if examples[s]:
            pid, txt = examples[s][0]
            print(f"  [{s}] {pid}: {txt[:130]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
