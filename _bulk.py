"""大批量抓取本方向论文，扩充语料。只管抓，不管过滤细节。"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '.')
from rharness.sources.arxiv import Paper, RateLimiter, collect      # noqa: E402
from rharness.sources.fulltext import attach_fulltext, corpus_fingerprint  # noqa: E402

# 覆盖导师各条研究线的检索式（用 PDE 社区专属词，避开 denoising 污染）
QUERIES = [
    'abs:"time-fractional" AND abs:"Allen-Cahn"',
    'abs:"time-fractional" AND abs:"Cahn-Hilliard"',
    'abs:"time-fractional" AND abs:"reaction-diffusion"',
    'abs:"Cahn-Hilliard" AND abs:"segmentation"',
    'abs:"Cahn-Hilliard" AND abs:"image"',
    'abs:"Allen-Cahn" AND abs:"regularization"',
    'abs:"Allen-Cahn" AND abs:"mean curvature"',
    'abs:"phase-field" AND abs:"surface reconstruction"',
    'abs:"phase field" AND abs:"point cloud"',
    'abs:"phase-field" AND abs:"topology optimization"',
    'abs:"phase-field" AND abs:"crystal growth"',
    'abs:"phase-field" AND abs:"dendrite"',
    'abs:"reaction-diffusion" AND abs:"Lengyel-Epstein"',
    'abs:"reaction-diffusion" AND abs:"Gray-Scott"',
    'abs:"Sine-Gordon" AND abs:"damping"',
    'abs:"Langevin" AND abs:"Allen-Cahn"',
    'abs:"Langevin" AND abs:"Cahn-Hilliard"',
    'abs:"Ginzburg-Landau" AND abs:"Allen-Cahn"',
    'abs:"phase-field crystal"',
    'abs:"Voronoi" AND abs:"lattice structure"',
    'abs:"mesh denoising" AND abs:"geometry"',
    'abs:"surface denoising" AND abs:"normal"',
    'abs:"level set" AND abs:"denoising"',
    'abs:"moving by mean curvature"',
    'cat:math.NA AND abs:"Allen-Cahn"',
    'cat:math.NA AND abs:"Cahn-Hilliard"',
    'cat:math.NA AND abs:"phase field"',
    'abs:"numerical" AND abs:"Allen-Cahn equation"',
    'abs:"preserving volume" AND abs:"phase"',
    'abs:"fractional" AND abs:"Gray-Scott"',
]

PER = int(sys.argv[1]) if len(sys.argv) > 1 else 12
lim = RateLimiter(3.0)
seen, papers, by_q = set(), [], {}

for q in QUERIES:
    try:
        got = collect(q, target=PER, limiter=lim)
    except Exception as e:                                   # noqa: BLE001
        print(f"[ERR] {q[:52]:<52} {e}", flush=True)
        continue
    new = 0
    for p in got:
        base = p.id.split("v")[0]
        if base in seen:
            continue
        seen.add(base)
        papers.append(p)
        new += 1
    by_q[q] = {"fetched": len(got), "new": new}
    print(f"[{new:>3}/{len(got):>2}] {q[:58]}", flush=True)

print(f"\n合计去重后 {len(papers)} 篇，开始下载全文…", flush=True)
st = attach_fulltext(papers, verbose=False)
print(f"下载={st['downloaded']} 缓存={st['cached']} 解析成功={st['parsed']} 失败={st['failed']}")

mf = {
    "generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "fingerprint": corpus_fingerprint(papers),
    "n_papers": len(papers),
    "n_with_fulltext": st["parsed"],
    "queries": by_q,
    "papers": [{"id": p.id, "title": p.title, "published": p.published,
                "categories": p.categories[:3], "has_fulltext": bool(p.fulltext),
                "n_chars": len(p.fulltext or "")} for p in papers],
}
Path("libraries/corpus_bulk.json").write_text(
    json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")
print("已写入 libraries/corpus_bulk.json")
