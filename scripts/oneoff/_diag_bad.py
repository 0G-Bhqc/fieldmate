"""诊断 6 份「拿到但解析不出正文」的 PDF：到底是残缺、加密，还是后端问题。

为什么要单独查
--------------
`unparsable` 这一档最容易被误判成「这文件坏了，重下就行」。但实测至少有
两种成因，处置方式相反：

  A. 文件本身残缺（下载被中途掐断）—— **重下能修**
     典型信号：尾部没有 %%EOF、文件头是 %PDF 但长度明显偏小。
  B. 文件完好，只是当前解析后端啃不动（加密/扫描件/异常字体编码）—— **重下没用**
     典型信号：%%EOF 齐全，pypdf 与 fitz 表现不一致。

把 A 和 B 混为一谈，就会对 B 反复重下（还撞 429），或者对 A 放弃治疗。
先分类，再决定动作。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, '.')
from _audit_cache import cache_path  # noqa: E402

from fieldmate.sources.local import parse_pdf_cached  # noqa: E402

CACHE = Path(".fieldmate-cache/arxiv_pdfs")
IDS = ["2102.05139v2", "2502.00509v1", "1704.02348v1",
       "2411.05840v1", "2410.04451v1", "2504.13967v1",
       "2609.24871v2"]          # 最后一个是「缺失」，一并看


def head(p: Path, n: int = 8) -> bytes:
    with p.open("rb") as f:
        return f.read(n)


def tail(p: Path, n: int = 2048) -> bytes:
    sz = p.stat().st_size
    with p.open("rb") as f:
        f.seek(max(0, sz - n))
        return f.read()


for aid in IDS:
    p = cache_path(CACHE, aid)
    print("=" * 68)
    if not p.exists():
        print(f"{aid}: 文件不存在 —— 纯 missing，需要下载")
        continue
    sz = p.stat().st_size
    h, t = head(p), tail(p)
    is_pdf = h.startswith(b"%PDF")
    has_eof = b"%%EOF" in t
    print(f"{aid}  {sz / 1e6:.2f} MB")
    print(f"  头 {h[:8]!r}  是PDF={is_pdf}")
    print(f"  尾含 %%EOF={has_eof}  尾样本={t[-24:]!r}")

    # 逐个后端单独试，看是谁在失败
    try:
        import pypdf
        r = pypdf.PdfReader(str(p))
        try:
            txt = "".join((pg.extract_text() or "") for pg in r.pages)
            print(f"  pypdf: {len(r.pages)} 页 / {len(txt)} 字符")
        except Exception as e:
            print(f"  pypdf: 读到了 {len(r.pages)} 页但抽文本失败 {type(e).__name__}: {e}")
    except Exception as e:
        print(f"  pypdf: 打不开 {type(e).__name__}: {e}")

    try:
        import fitz
        d = fitz.open(str(p))
        txt = "".join(pg.get_text() for pg in d)
        print(f"  fitz : {d.page_count} 页 / {len(txt)} 字符")
    except Exception as e:
        print(f"  fitz : 打不开 {type(e).__name__}: {e}")

    txt, backend = parse_pdf_cached(p)
    # parse_pdf_cached 失败时返回 (None, backend)，不是空串 —— 这是 D-REP-001 的纪律。
    # 这里显式处理，别让 len(None) 把诊断脚本自己带崩。
    print(f"  parse_pdf_cached -> {backend} / {len(txt) if txt else 0} 字符")
