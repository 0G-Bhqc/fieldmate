"""验证假设：PyMuPDF 把「章节标题 + 首段正文」合并成了同一行。

这决定了修法方向
--------------
现有 SECTION_PATTERNS 全部要求整行只有标题（末尾锚 `\\s*$`）。
如果标题确实总与正文同行，那么再怎么调「严格程度」都是错的 ——
调松会重演首版事故（`method` 命中 17 次，正文里以数字开头的行全被当标题），
调紧则继续漏（实测 method 只有 12%）。

所以先量三件事：
  1. 已知章节关键词在**行首任意位置**能捞到多少篇（vs 整行匹配）
  2. 命中的那一行，标题后面是否紧跟正文（合并假设的直接证据）
  3. 有多少命中是假阳性（行首关键词其实是正文，如 "3. We consider..."）
"""
from __future__ import annotations

import re
import sys
from collections import Counter

sys.path.insert(0, '.')
from fieldmate.sources.fulltext import load_manifest          # noqa: E402

# 行首匹配：可选编号 + 章节关键词，后跟分隔符或词边界
HEAD_KW = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?|[IVXLC]+\.|[A-H]\.)?\s*"
    r"(introduction|abstract|related\s+work|background|"
    r"numerical\s+(?:method|scheme|experiments?|results?|discretization|approximation|"
    r"validation|test)|"
    r"discretization|formulation|governing\s+equations?|methodology|algorithm|"
    r"proposed\s+(?:method|model|approach)|our\s+method|the\s+proposed\s+\w+|"
    r"experiments?|results?|evaluation|simulations?|test\s+cases?|"
    r"conclusions?|discussion|summary|future\s+work|limitations?|references|bibliography)"
    r"\b",
    re.I)

MERGED = Counter()
hits_paper = 0
examples = []
fp = []          # 看着像假阳性的
n = 0

papers, _ = load_manifest("libraries/corpus_bulk.json")
for p in papers:
    if not p.fulltext:
        continue
    n += 1
    got_here = False
    for i, ln in enumerate(p.fulltext.splitlines()):
        s = ln.strip()
        if not (3 <= len(s) <= 300):
            continue
        m = HEAD_KW.match(s)
        if not m:
            continue
        got_here = True
        rest = s[m.end():]
        kw = m.group(1).lower()
        # 标题结束、后面还剩东西 -> 合并假设成立
        if rest[:1] in (".", ":", "") and rest.strip() and not rest.strip()[0].isdigit():
            MERGED[kw] += 1
            if len(examples) < 12:
                examples.append((p.id, i, s[:150]))
        # 关键词后面直接跟句子（没有句号分隔）-> 多半是正文行
        elif rest and rest[0].islower():
            fp.append((p.id, i, s[:120]))
    hits_paper += got_here

print(f"语料 {n} 篇")
print(f"行首关键词能捞到：{hits_paper} / {n} ({hits_paper/n:.0%})")
print(f"  现有整行匹配 method 只有 12% —— 差距就是这个\n")
print(f"其中「标题后紧跟正文」（合并证据）：{sum(MERGED.values())} 行")
for kw, c in MERGED.most_common(12):
    print(f"   {kw:<28}{c}")
print(f"\n看着像假阳性（关键词后直接跟正文小写词）：{len(fp)} 行")
for pid, i, s in fp[:10]:
    print(f"   {pid} L{i}: {s}")

print("\n===== 合并证据样例 =====")
for pid, i, s in examples:
    print(f"  {pid} L{i}\n     {s}")
