"""续传下载：跳过已缓存 -> 直接按 id 下 PDF -> 增量回写 manifest。

用法： python _resume.py <批次号> <每批篇数>
缓存目录里已有的 PDF 会直接复用，所以可以反复运行直到 missing=0。

为什么不再逐篇调 arXiv API 拿元数据
----------------------------------
早先版本对每条缺失记录先 `collect(id:xxx)` 再下载，等于**每篇论文发两次请求**
（一次 API + 一次 PDF）。但 PDF 地址完全由 arXiv id 决定
（见 fulltext._candidate_urls），那次 API 调用对下载没有任何信息增量，
却把限流暴露面直接翻倍。去掉它之后：
  * 请求数减半；
  * 429 的机会随之减半；
  * 且不再受 API 侧限流影响 —— 只剩 PDF 路径的限流。
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, '.')
from fieldmate.sources.arxiv import Paper, RateLimiter  # noqa: E402
from fieldmate.sources.fulltext import BULK_INTERVAL, attach_fulltext  # noqa: E402
from fieldmate.sources.local import parse_pdf_cached  # noqa: E402

BATCH = int(sys.argv[1]) if len(sys.argv) > 1 else 0
SIZE = int(sys.argv[2]) if len(sys.argv) > 2 else 40

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
    'abs:"level set" AND abs:"denoising"',
    'abs:"moving by mean curvature"',
    'cat:math.NA AND abs:"Allen-Cahn"',
    'cat:math.NA AND abs:"Cahn-Hilliard"',
    'cat:math.NA AND abs:"phase field"',
    'abs:"numerical" AND abs:"Allen-Cahn equation"',
    'abs:"preserving volume" AND abs:"phase"',
    'abs:"fractional" AND abs:"Gray-Scott"',
]

MF = Path("libraries/corpus_bulk.json")
CACHE = Path(".fieldmate-cache/arxiv_pdfs")


def save(entries, n_papers, extra=None):
    mf = {"generated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
          "n_papers": n_papers,
          "n_with_fulltext": sum(1 for e in entries if e["has_fulltext"]),
          "queries": QUERIES, "papers": entries}
    mf.update(extra or {})
    MF.write_text(json.dumps(mf, ensure_ascii=False, indent=2), encoding="utf-8")
    return mf


if not MF.exists():
    print(f"错误：{MF} 不存在。先跑一次 _bulk.py 检索生成列表。", flush=True)
    sys.exit(1)

mf = json.loads(MF.read_text(encoding="utf-8"))
entries = mf["papers"]


def cached_ok(e):
    """缓存里已有且能解析就算完成。解析失败的不置位 —— 它仍算缺失，下批重试。

    失败时必须**显式**把 has_fulltext 打回 False。早先只在成功分支写 True，
    于是 manifest 里上一轮留下的 True 会原样保留：条目文件在、解析不出来，
    manifest 却仍声称「有全文」。那会让 `n_with_fulltext` 高报，
    下游按这个数判断「这批语料够不够做证据」，结论就建立在假账上。
    （实测踩到过：236+7≠247，差额就出在这里。）

    用 `parse_pdf_cached` 而非 `parse_pdf`：这个函数在每次启动时对**全部**条目
    跑一遍，实测 247 份 / 1.18 GB 直解要 76s，换缓存版 ~20s。
    续传本来就可能反复运行，每次都重付这笔钱不合理。
    """
    p = CACHE / f"{e['id'].split('v')[0]}.pdf"
    if p.exists() and p.stat().st_size > 5000:
        t, _ = parse_pdf_cached(p)
        if t:
            e["has_fulltext"] = True
            e["n_chars"] = len(t)
            return True
    e["has_fulltext"] = False
    e.pop("n_chars", None)
    return False


n_cached = sum(1 for e in entries if cached_ok(e))
missing = [e for e in entries if not e["has_fulltext"]]
print(f"缓存可用 {n_cached} / {len(entries)}；仍缺 {len(missing)} 篇", flush=True)

# BATCH>=0 时只取第 BATCH 个 SIZE 大小的块（用于单批试跑）；
# BATCH<0 时循环处理**所有**缺失项，每块处理完立刻落盘。
# 分块+增量落盘的意义：全量下载要半小时量级，中途被 Ctrl-C / 超时打断时，
# 已下到的 PDF 和已回写的条目必须已经持久化，不能全押在最后一次写文件上。
todo = missing if BATCH < 0 else missing[BATCH * SIZE:(BATCH + 1) * SIZE]
start_at = 0 if BATCH < 0 else BATCH
print(f"待处理 {len(todo)} 篇（间隔 {BULK_INTERVAL}s，429/503 走 30/60/120/240s 退避）",
      flush=True)

done = 0
for off in range(0, len(todo), SIZE):
    chunk = todo[off:off + SIZE]
    if not chunk:
        break
    tag = start_at + off // SIZE
    print(f"--- 批次 {tag}：{len(chunk)} 篇 ---", flush=True)
    # 直接由 manifest 构造 Paper，不再回查 API：PDF 地址完全由 id 决定，
    # 那次 API 调用对下载没有信息增量，却把限流暴露面翻倍。
    papers = [Paper(id=e["id"], title=e.get("title", ""), abstract="",
                    published=e.get("published", ""),
                    categories=e.get("categories", [])) for e in chunk]
    st = attach_fulltext(papers, verbose=False, limiter=RateLimiter(BULK_INTERVAL))
    got = {p.id.split("v")[0]: len(p.fulltext or "") for p in papers if p.fulltext}
    for e in entries:
        k = e["id"].split("v")[0]
        if k in got:
            e["has_fulltext"] = True
            e["n_chars"] = got[k]
    done += st["parsed"]
    # 每块结束立刻落盘
    mf2 = save(entries, mf["n_papers"],
               {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "last_batch": tag})
    print(f"    下载={st['downloaded']} 解析成功={st['parsed']} "
          f"未取得={st['throttled']} | 累计有全文 {mf2['n_with_fulltext']}"
          f"/{mf2['n_papers']}", flush=True)
    if st["downloaded"] == 0 and st["parsed"] == 0:
        print("    本块颗粒无收（很可能仍在限流窗口内），停止后续批次。", flush=True)
        break

mf2 = save(entries, mf["n_papers"],
           {"updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
print(f"\n完成：有全文 {mf2['n_with_fulltext']} / {mf2['n_papers']}（本次新增 {done}）",
      flush=True)
