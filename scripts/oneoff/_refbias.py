"""量化「参考文献区污染检测结果」的影响。

问题
----
`Paper.text_for("fulltext")` 返回整份 PDF 文本，**含参考文献区**。
而所有 `absent` 类型的检测信号（报时间步 / 报分辨率 / 报稳定条件 / 开源自复现…）
都是在这份文本里搜「有没有出现过」。

后果：一篇论文只要**引用**了报 Δt 的工作，它自己就会被判成「报了 Δt」。
于是所有缺失率都**系统性偏低** —— 报得越少，缺得越不明显，机会越看不出。

这个偏差对「开源自复现」尤其致命：只要正文提到过任何 github 链接
（包括别人工作的、或引言里"code is available at..."的泛指）就算命中。

先量，别先改
------------
改动前必须知道影响是 2 个百分点还是 20 个百分点。量法：
同一批 247 篇，同一套规则，分别用「整份全文」和「砍掉参考文献后的正文」跑一遍，
逐项对比。
"""
from __future__ import annotations

import re
import sys

sys.path.insert(0, '.')
from fieldmate.compare.matrix import REPORT_ITEMS  # noqa: E402
from fieldmate.sources.fulltext import load_manifest  # noqa: E402

# 参考文献区的起始。用行首匹配 + 短行要求，避免把正文里提到的
# "the references [3] show" 当成参考文献区。
REF_START = re.compile(
    r"^\s*(?:\d+(?:\.\d+)*\.?\s*|[IVXLC]+\.?\s*)?"
    r"(?:references|bibliography|reference\s+list|literature\s+cited)\s*[:.]?\s*$",
    re.IGNORECASE)


def find_ref_cut(lines: list[str]) -> int:
    """返回参考文献区起始行号；找不到返回 -1。

    从**后往前**找：正文里也常有 "References" 字样（"see the references"），
    但真正的参考文献区一定在靠后位置；取最后一个候选更稳。
    还要过一道长度门槛：参考文献区标题后面应该紧跟文献条目，
    前面就是正文，所以不能出现在文件极开头。
    """
    best = -1
    start = max(1, int(len(lines) * 0.25))          # 前 25% 不可能是参考文献区
    for i in range(len(lines) - 1, start - 1, -1):
        s = lines[i].strip()
        if not s or len(s) > 60:
            continue
        if REF_START.match(s):
            best = i
            break
    return best


def main() -> int:
    papers, _ = load_manifest("libraries/corpus_bulk.json", verbose=False)

    stats = {name: [0, 0, 0] for name, _p, _d in REPORT_ITEMS}  # [全文命中, 正文命中, 总数]
    no_cut = 0
    cuts = []
    for p in papers:
        if not p.fulltext:
            continue
        lines = p.fulltext.splitlines()
        cut = find_ref_cut(lines)
        if cut < 0:
            no_cut += 1
            body = p.fulltext
        else:
            cuts.append(cut / len(lines))
            body = "\n".join(lines[:cut])
        whole = f"{p.title}\n{p.abstract}\n{p.fulltext}"
        bod = f"{p.title}\n{p.abstract}\n{body}"
        for name, pat, _d in REPORT_ITEMS:
            stats[name][0] += bool(re.search(pat, whole, re.IGNORECASE))
            stats[name][1] += bool(re.search(pat, bod, re.IGNORECASE))
            stats[name][2] += 1

    n = sum(1 for p in papers if p.fulltext)
    print(f"语料 {n} 篇有全文；未能定位参考文献区 {no_cut} 篇（{no_cut/n:.0%}）\n")
    if cuts:
        cuts.sort()
        print(f"参考文献区起始位置（占全文比例）中位数 {cuts[len(cuts)//2]:.0%}，"
              f"最早 {cuts[0]:.0%}\n")
    print(f"{'检测项':<14}{'全文口径':>10}{'正文口径':>10}{'缺失率变化':>14}")
    print("-" * 52)
    for item, (hf, hb, tot) in stats.items():
        if not tot:
            continue
        mf, mb = 1 - hf / tot, 1 - hb / tot
        d = mb - mf
        flag = "  <-- 偏差大" if abs(d) >= 0.05 else ""
        print(f"{item:<14}{mf:>9.0%}{mb:>10.0%}{d:>+13.1%}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
