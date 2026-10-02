"""读 JAX-PF 的 benchmark，抽出对导师拓扑优化线有用的部分。

为什么读它
----------
`fieldmate topics` 报「可微求解器 2 篇」，其中一篇就是 JAX-PF（2601.06079）。
它是这个方向少见的**公开、完整、带基准**的实现：AC / CH / 耦合 AC-CH 四组，
显式与隐式两套时间积分，还有 Eshelby 包含。

本项目缺陷库里与「可微/隐式/量纲」相关的三条：
  D-RES-001  时间步量纲随 Laplacian 是否带 ε² 前缀而改变
  D-RES-002  CH 含四阶算子，显式步长限制严于 AC
  D-MOD-004  双阱项再除以 ε_w² 会使刚性远超显式步承受范围

这三���都是**我们自己踩出来的**。JAX-PF 用 JAX 自动微分 + 自定义伴随，
在机器精度下处理了同一类问题 —— 它给的稳定性条件、参数区间、
以及「为什么隐式比显式贵在哪」，可以直接对照，值得抄。

本脚本只抽**可核对的事实**（原文片段 + 出处），不下结论。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, '.')
from fieldmate.sources.local import parse_pdf_cached            # noqa: E402

PDF = Path(".fieldmate-cache/arxiv_pdfs/2601.06079.pdf")

# 逐个探针：关键词 -> 关注什么
PROBES = {
    "CFL / 稳定性条件": r"CFL|Courant|stable time step|stability condition|"
                        r"stability constraint|explicit.{0,20}requires",
    "隐式 vs 显式": r"implicit.{0,60}(?:expensive|cost|Ny|NL solve)|"
                    r"cheaper than implicit|compared to implicit",
    "ε 的量纲 / 界面宽度": r"epsilon\w*\s*(?:=|is)\s*|interface width|"
                          r"thin interface|epsilon_w|double well",
    "时间步取值": r"time step (?:of|size|Δt|is set|was)\s*[\w\d.]+|"
                 r"Δt\s*=|dt\s*=\s*[\d.]+|step size of\s*[\d.]+",
    "网格 / 分辨率": r"mesh of|grid of|elements|resolution|h\s*=\s*[\d./]+|"
                  r"N\s*=\s*\d+",
    "参数标定 / 反演": r"calibrat\w+|inverse design|parameter identification|"
                     r"gradient-based|adjoint",
    "已知局限": r"limitation|however|drawback|fails? to|"
               r"does not (?:consider|handle|account|guarantee)|remains? open",
    "代码可用性": r"github|code (?:is )?available|open.source|reproduc",
}


def main() -> int:
    if not PDF.exists():
        print(f"缺少 {PDF}")
        return 1
    text, backend = parse_pdf_cached(PDF)
    if not text:
        print("解析失败")
        return 1
    low = text.lower()
    print(f"{PDF.name}  后端={backend}  {len(text):,} 字符\n")

    for name, pat in PROBES.items():
        print("=" * 70)
        print(name)
        print("=" * 70)
        hits = list(re.finditer(pat, low))
        if not hits:
            print("  （无命中）\n")
            continue
        print(f"  命中 {len(hits)} 处，前 4 条：")
        for m in hits[:4]:
            seg = text[max(0, m.start() - 180):m.end() + 220]
            seg = re.sub(r"\s+", " ", seg).strip()
            print(f"    … {seg} …")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
