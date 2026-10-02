"""语料缓存盘点：只读、纯本地、不发任何网络请求。

为什么需要这个
--------------
`_resume.py` 的启动阶段要逐条判断「缓存里这篇到底有没有全文」。
早期它直接调 `parse_pdf`，那是**每次运行都要把 253 份 / 1.18 GB 的 PDF
重新解析一遍**（实测单份 0.15~0.77s，大文件更久）。下载本来是分钟级的事，
结果被这个自查拖成分钟级，而且**重复运行要重复付这个代价**。

用 `parse_pdf_cached` 后单份降到 ~0.06s，且第二次起走磁盘缓存。

三态输出，不是两态
------------------
    parsed      文件在、解析得出正文      -> 可用于 STRONG 级证据
    unparsable  文件在、解析不出正文      -> 有缓存但不可用（残缺/扫描件/加密）
    missing     文件不在或过小            -> 从未成功下载

把 `unparsable` 单独拎出来，是因为它和 `missing` 的含义完全相反：
`missing` 是**我们没拿到**（可以去补），`unparsable` 是**拿到了但用不了**
（再下多少次都一样，得换后端或人工处理）。混在一起会导致「反复重下那些
根本下不好的文件」这种无效劳动，还会让人低估语料的真实状态。

用法： python _audit_cache.py [--write]
       --write  把 has_fulltext / n_chars 回写进 manifest（默认只读不改）
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, '.')
from fieldmate.sources.local import parse_pdf_cached          # noqa: E402

MF = Path("libraries/corpus_bulk.json")
CACHE = Path(".fieldmate-cache/arxiv_pdfs")
MIN_BYTES = 5000          # 与 fulltext._download / load_manifest 保持一致


def cache_path(cache: Path, arxiv_id: str) -> Path:
    base = re.sub(r"v\d+$", "", str(arxiv_id))
    return cache / f"{re.sub(r'[^A-Za-z0-9._-]', '_', base)}.pdf"


def audit(entries: list[dict], cache: Path) -> dict:
    parsed, unparsable, missing = [], [], []
    t0 = time.time()
    for e in entries:
        aid = str(e.get("id", "")).strip()
        p = cache_path(cache, aid)
        if not p.exists() or p.stat().st_size <= MIN_BYTES:
            missing.append((aid, 0))
            continue
        text, _ = parse_pdf_cached(p)
        if text:
            parsed.append((aid, len(text)))
            e["has_fulltext"] = True
            e["n_chars"] = len(text)
        else:
            unparsable.append((aid, p.stat().st_size))
    return {"parsed": parsed, "unparsable": unparsable, "missing": missing,
            "seconds": time.time() - t0}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="回写 manifest")
    ap.add_argument("--manifest", default=str(MF))
    ap.add_argument("--cache", default=str(CACHE))
    a = ap.parse_args()

    mp, cp = Path(a.manifest), Path(a.cache)
    if not mp.exists():
        print(f"错误：manifest 不存在 {mp}", file=sys.stderr)
        return 1

    mf = json.loads(mp.read_text(encoding="utf-8"))
    entries = mf.get("papers") or []
    r = audit(entries, cp)
    p, u, m = r["parsed"], r["unparsable"], r["missing"]

    lens = sorted(n for _, n in p)
    print(f"清单 {len(entries)} 篇   盘点耗时 {r['seconds']:.1f}s")
    print(f"  可解析   {len(p):4d}   <- 可用于 STRONG 级证据")
    print(f"  不可解析 {len(u):4d}   <- 有文件但用不了，重下无用")
    print(f"  缓存缺失 {len(m):4d}   <- 没拿到，可以补")
    if lens:
        print(f"  正文字符  中位 {lens[len(lens) // 2]:,}  合计 {sum(lens):,}")
    if u:
        print("\n-- 不可解析明细（拿到但用不了）--")
        for aid, sz in u:
            print(f"   {aid:20s} {sz / 1e6:6.2f} MB")
    if m:
        print(f"\n-- 缓存缺失前 15 个（共 {len(m)}）--")
        for aid, _ in m[:15]:
            print(f"   {aid}")

    if a.write:
        mf["n_with_fulltext"] = len(p)
        mf["n_unparsable"] = len(u)
        mf["n_missing"] = len(m)
        mp.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n已回写 {mp}")
    else:
        print("\n（只读模式，未改动 manifest；加 --write 回写）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
