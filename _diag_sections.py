"""看真实 PDF 抽出来的文本里，章节标题行到底长什么样。

为什么必须看原始行而不是继续调正则
--------------------------------
实测：method 章节只在 12% 的论文里检出，intro 49%、related 5% ——
真实 arXiv 数学论文几乎必然有 Introduction，却只有不到一半被认出来。
这说明**问题不在「正则太严」这一个方向**：调松会重演首版
（`method` 命中 17 次，因为正文里以数字开头的行全被当标题），
调紧则继续漏。得先看清数据长什么样，才知道该往哪边动。
"""
from __future__ import annotations

import re
import sys
from collections import Counter

sys.path.insert(0, '.')
from rharness.sources.fulltext import load_manifest          # noqa: E402

# 真实章节标题的典型形态：短行、以数字或全大写开头
HEADISH = re.compile(
    r"^\s*(?:"
    r"\d+(?:\.\d+)*\.?\s+"                 # 1. / 3.2 / 4.1.2
    r"|[IVXLC]+\.\s+"                      # 罗马数字
    r"|[A-Z][A-Z\s\-]{4,}"                 # 全大写标题
    r"|(?:Appendix\s+)?[A-H]\.?\s+"        # 附录式 A. / B.
    r")\s*(\S.*?)\s*$")

papers, _ = load_manifest("libraries/corpus_bulk.json")
print(f"语料 {len(papers)} 篇\n")

head_titles = Counter()
per_paper = []
for p in papers[:60]:
    if not p.fulltext:
        continue
    lines = p.fulltext.splitlines()
    found = []
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not (3 <= len(s) <= 90):          # 章节标题都很短
            continue
        m = HEADISH.match(s)
        if m:
            title = m.group(1)
            # 排除明显是正文句子的：句中有多个普通单词且以小写开头的长句
            words = title.split()
            if len(words) <= 12 and not title.endswith((".", ",", ";")):
                found.append((i, s))
                head_titles[title] += 1
    per_paper.append((p.id, found))

n = len(per_paper)
print(f"前 {n} 篇里，用宽松启发式能捞到的标题行共 "
      f"{sum(len(f) for _, f in per_paper)} 条，Top 40：\n")
for t, c in head_titles.most_common(40):
    print(f"  {c:>3}  {t}")

print("\n\n===== 逐篇看前 3 篇的全部候选标题 =====")
for pid, found in per_paper[:3]:
    print(f"\n--- {pid} （{len(found)} 条）---")
    for i, s in found[:25]:
        print(f"   L{i:<5} {s}")
