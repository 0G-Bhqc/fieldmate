"""语料覆盖率自查：目标论文的关键技术线，在 247 篇语料里到底有没有支撑。

先说清楚为什么**不**做自动术语抽取
--------------------------------
第一版试过「从目标论文里自动抽术语再查语料」，结果抽出来的是
Jalili / Wang / Kim / Ganji（作者名）、Stanford / Comput / Phys（引文残渣）、
Then / Therefore / After（连接词）—— 唯一那个「0 命中」的词是 Ganji，
一个作者名。**这个测量本身是垃圾，不能用来下任何结论。**

根因有二，都不是调参能解决的：
  1. 单词的词形不携带「这是术语」的信息。作者名和术语在字面上同构。
  2. 论文里出现 >=2 次就入选，而参考文献区天然让每个引用都出现 2 次。

所以改成**显式词表**：每个词都是人根据课题内容写下的、可逐条核对的声明，
和 `libraries/defect_patterns.jsonl` 用同一条标准 ——
「写下来的每一条都必须能被另一个人独立复核」。

覆盖率口径与局限
----------------
命中 = 语料里至少一篇论文的**标题+全文**出现该词（允许 `phase field` /
`phase-field` 两种写法）。这是词面统计：
  * 命中 **不等于**相关 —— 「Langevin」可能指随机过程而非 Langevin 方程；
  * 未命中 **不等于**无关 —— 同义写法、不同符号约定都会让统计偏低。
所以 0 命中是强告警，非 0 命中什么也证明不了。这个不对称性要写进输出。
"""
from __future__ import annotations

import re
import sys

sys.path.insert(0, '.')
from fieldmate.sources.fulltext import load_manifest  # noqa: E402

# 课题组研究线的关键术语。每条附 note，说明它为什么必须在语料里有支撑。
TERMS: list[tuple[str, str]] = [
    ("Allen-Cahn", "核心模型，导师两篇论文的主方程"),
    ("Cahn-Hilliard", "四阶保体积模型，保体积类工作的对照组"),
    ("time-fractional", "导师 2025-2026 最活跃的线（归一化时间分数阶）"),
    ("fractional", "同上；更宽的写法"),
    ("Langevin", "随机梯度流，导师核心工具之一"),
    ("Langevin equation", "同上，明确到方程形式"),
    ("damping limit", "Sine-Gordon 阻尼极限，另一条核心线"),
    ("Sine-Gordon", "同上"),
    ("Gray-Scott", "导师主页有归一化时间分数阶 Gray-Scott"),
    ("Lengyel-Epstein", "导师主页有归一化时间分数阶 Lengyel-Epstein"),
    ("Navier-Stokes", "导师主页有分数阶 Navier-Stokes"),
    ("fidelity term", "Allen-Cahn + 保真项，modified AC"),
    ("modified Allen-Cahn", "同上"),
    ("Voronoi", "导师的 Voronoi 晶格线"),
    ("mean curvature", "曲面重建的几何侧"),
    ("phase field", "本课题的方法族名"),
    ("surface reconstruction", "论文①的直接可比方向"),
    ("point cloud", "论文②与 pfdenoise 的定位"),
    ("mesh denoising", "论文②的经典基线"),
    ("level set", "同类基线方法"),
    ("topology optimization", "相场拓扑优化（人工骨植入体）"),
    ("gradient flow", "贯穿全部方法的上位概念"),
    ("volume preservation", "保体积，核心卖点之一"),
    ("energy stability", "导师「Cahn-Hilliard + 能量稳定性」那条线"),
]


def _pat(term: str) -> re.Pattern[str]:
    parts = [x for x in re.split(r"[\s\-_]+", term.lower()) if x]
    return re.compile(r"\b" + r"[\s\-]+".join(re.escape(x) for x in parts) + r"\b")


def main() -> int:
    papers, _ = load_manifest("libraries/corpus_bulk.json", verbose=False)
    blob = [(p.id, (p.title + "\n" + (p.fulltext or "")).lower()) for p in papers
            if p.fulltext]
    print(f"语料：{len(blob)} 篇有全文\n")
    print(f"{'术语':<24}{'命中篇数':>9}   说明")
    print("-" * 78)
    zero: list[tuple[str, str]] = []
    thin: list[tuple[str, str, int]] = []
    for term, note in TERMS:
        pat = _pat(term)
        hits = [pid for pid, b in blob if pat.search(b)]
        n = len(hits)
        if n == 0:
            zero.append((term, note))
        elif n < 5:
            thin.append((term, note, n))
        mark = "  <-- 0 命中" if n == 0 else ("  <-- 支撑薄弱" if n < 5 else "")
        print(f"{term:<24}{n:>9}   {note}{mark}")

    print(f"\n{'=' * 78}")
    print(f"0 命中 {len(zero)} 条 / 支撑薄弱(<5 篇) {len(thin)} 条 / 共 {len(TERMS)} 条")
    if zero:
        print("\n0 命中的技术线 —— 语料对这几条**没有支撑**：")
        for t, n in zero:
            print(f"  · {t:<22} {n}")
        print("\n这些不是「检索式写得不好」，而是这些线**整体不在 arXiv 上**。")
    if thin:
        print("\n支撑薄弱（<5 篇），横向对比的统计功效不足：")
        for t, n, c in thin:
            print(f"  · {t:<22} {c} 篇   {n}")
    print("\n口径提醒：命中=词面出现在标题或全文。命中不等于相关，"
          "未命中也不等于无关（同义写法会偏低）。0 命中是强告警，非 0 命中什么也证明不了。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
