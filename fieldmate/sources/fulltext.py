"""arXiv 全文抓取与缓存

为什么这是必需件（不是可选优化）
--------------------------------
只读标题/摘要时，本框架**几乎不可能**产出 STRONG 级证据。原因很直接：
arXiv 摘要是 150~250 词，**没人会在摘要里写 Δt、ε、网格分辨率**。
所以「摘要中未提及时间步」测的是「摘要这种体裁会不会写这个」，
而不是「论文有没有报告这个」。

实测：7 篇纯摘要的检索，产出的 6 条精进点候选**全部是 WEAK/MEDIUM**，
框架按自己的纪律明确拒绝把它们当成立项依据（退出码 4）。这是正确的，
但也说明没有全文的横向对比**只能当线索，不能当证据**。

所以：抓全文是把框架从「线索机」升级为「证据机」的必要条件。
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .arxiv import BULK_INTERVAL, Paper, RateLimiter, backoff_seconds
from .local import parse_pdf, parse_pdf_cached

PDF_TMPL = "https://arxiv.org/pdf/{id}"


def default_cache() -> Path:
    return Path(".fieldmate-cache/arxiv_pdfs")


def _pdf_path(cache: Path, arxiv_id: str) -> Path:
    # 把版本号去掉，避免 v1/v2 各存一份
    base = re.sub(r"v\d+$", "", arxiv_id)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", base)
    return cache / f"{safe}.pdf"


# 候选写法按实测成功率排序（2026-10，三个代表性 id 全量对拍）：
#
#   取法                          2509.07862  2501.01234  1704.02348
#   export.arxiv.org/pdf/{base}       200         200         200     3/3
#   arxiv.org/pdf/{base}              406         200       IncompleteRead
#   arxiv.org/pdf/{base}.pdf          406         406         406     0/3
#
# 两条实测结论，都与直觉相反，值得写下来免得以后被「优化」掉：
#   1. **决定成败的是主机，不是 User-Agent。** 同一地址换三种 UA
#      （浏览器型 / fieldmate 型 / 带 Referer）结果完全一致，
#      全是 406 或全是 200。早先注释里「PDF 请求改用浏览器 UA 更稳」是错的。
#   2. **带 `.pdf` 后缀那种写法是最差的**，却是很多示例代码的默认写法。
#
# 顺序仍保留「学出来」的机制：不同网络环境下主机的可达性可能反过来，
# 记住最近成功的那一种即可，不必把实测结论当成永恒真理。
_URL_SHAPES = ("export_base", "www_base", "www_suffixed", "export_versioned")
_PREFERRED_SHAPE: list[str] = list(_URL_SHAPES)   # list 便于原地改顺序


def _url_for(shape: str, arxiv_id: str, base: str) -> str:
    if shape == "export_base":
        return f"http://export.arxiv.org/pdf/{base}"
    if shape == "www_base":
        return f"https://arxiv.org/pdf/{base}"
    if shape == "www_suffixed":
        return f"https://arxiv.org/pdf/{base}.pdf"
    if shape == "export_versioned":
        return f"http://export.arxiv.org/pdf/{arxiv_id}"
    return f"http://export.arxiv.org/pdf/{base}"


def _earlier_versions(arxiv_id: str, max_back: int = 3) -> list[str]:
    """同一个条目的更早版本号，从新到旧。

    实测：`2609.24871v2` 在 export 上 **v2 和无版本号双双 404，只有 v1 拿得到**
    （10.6 MB，完整）。也就是说「取最新版本」这个默认假设并不总是成立 ——
    新版本可能还没同步 PDF，或者被撤下。

    没有这条回退时，这一篇会被永久判成「arXiv 上没有 PDF」，
    而真相是「有，只是版本对不上」。前者会污染语料统计，后者只是少一篇。
    """
    m = re.match(r"^(.*?)v(\d+)$", str(arxiv_id))
    if not m:
        return []
    stem, n = m.group(1), int(m.group(2))
    if n <= 1:
        return []
    return [f"{stem}v{v}" for v in range(n - 1, max(n - 1 - max_back, 0), -1)]


def _candidate_urls(arxiv_id: str) -> list[str]:
    """一个条目的多个候选 PDF 地址，按「最近成功过的写法」优先。

    各写法各有各的失败模式，所以都要保留；但顺序是学出来的，不是写死的。
    结果去重：id 不带版本号时 base 与 versioned 会退化成同一个地址，
    重复试一遍纯属白烧一轮重试。
    """
    base = re.sub(r"v\d+$", "", arxiv_id)
    out: list[str] = []
    for s in _PREFERRED_SHAPE:
        u = _url_for(s, arxiv_id, base)
        if u not in out:
            out.append(u)
    # 版本回退放最后：前面几种写法都失败之后才走，且只走 export 主机
    # （那是实测三种写法里唯一 3/3 命中的一种）。
    for v in _earlier_versions(arxiv_id):
        u = f"http://export.arxiv.org/pdf/{v}"
        if u not in out:
            out.append(u)
    return out


def _looks_complete_pdf(data: bytes) -> bool:
    """这份字节流像一份**完整**的 PDF 吗？

    实测代价：429 风暴期间抓到 6 份被截断的 PDF，全部长这样 ——
        头部 %PDF-1.5 正常、尾部没有 %%EOF、
        pypdf 报 `Stream has ended unexpectedly`、fitz 读到 0 页。
    三个解析后端一致失败，且失败模式完全相同，这就排除了「后端不支持某种
    加密/字体编码」的解释 —— 是文件本身没下完。

    只看头会漏（截断文件的头永远是好的），只看大小也会漏（1MB 的残片完全
    可能过 5000 字节的门槛）。**`%%EOF` 是 PDF 规范里唯一可靠的完整性锚点**，
    而且允许尾部有若干字节的空白，所以从后往前找而不是要求精确等于末尾。
    """
    if not data.startswith(b"%PDF"):
        return False
    return b"%%EOF" in data[-2048:]


def _download(url: str, dest: Path, limiter: RateLimiter,
              retries: int = 3, timeout: int = 90) -> tuple[bool, str]:
    """下载单个文件。

    健壮性要点：
      1. arXiv 的 PDF 动辄 5~20 MB，实测会出现
         `http.client.IncompleteRead`（连接中途断开）。这个异常**不在**
         urllib.error 的子类里，必须单独捕获 —— 漏掉它会让工具在
         第一次网络抖动时直接崩掉，而不是跳过一篇继续跑。
      2. IncompleteRead 的残片**不再直接当成品**（见下方第 5 条）。
      3. 406 与 User-Agent 无关：实测同一地址换浏览器型 / fieldmate 型 /
         带 Referer 三种 UA，结果逐条一致。406 的真正开关是**主机**
         （见 _candidate_urls 的对拍表）。这里保留一个明确的 UA
         只是为了表明身份，不是为了绕过 406 —— 别再把它当调优旋钮。
      4. 429 必须走长退避（见 arxiv.backoff_seconds）：批量下载撞限流是常态，
         2s 级重试等于没重试，会把后续几十篇全部判成「下载失败」——
         那不是 arXiv 没有 PDF，是我们自己被封了。**限流与「无此条目」是两件事，
         不能混为一谈**，所以这里不静默跳过，而是退避后重试到底。
      5. **残片不落 `.pdf` 名。** 早先版本把 >5000B 的 IncompleteRead 残片直接
         写进最终文件并返回成功，后果是永久性污染：下次运行看到
         「文件存在且 >5000B」就判定已缓存、永远跳过，那篇论文从此再也不会
         被重下，语料里就留下一个永远读不出正文的僵尸条目。宁可让它保持
         「缺失」状态、被下轮重试，也不能假装成功。现在一律先写 `.part`，
         校验通过才改名。
    """
    import http.client

    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".pdf.part")
    last = ""
    for attempt in range(retries):
        limiter.wait()
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (compatible; fieldmate/0.2)",
                              "Accept": "application/pdf,*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
            if len(data) < 5000:                    # 多半是错误页
                last = f"响应过小（{len(data)}B）"
            elif not _looks_complete_pdf(data):      # 残片，见第 5 条
                last = f"响应不完整（{len(data)}B，无 %%EOF）"
            else:
                dest.write_bytes(data)
                part.unlink(missing_ok=True)
                return True, "ok"
        except http.client.IncompleteRead as e:
            # 残片只留在 .part 里等下一轮重下，绝不冒充成品
            part_ = e.partial or b""
            if len(part_) > 5000:
                part.write_bytes(part_)
            last = f"IncompleteRead(partial={len(part_)}B，已留存待重下)"
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, OSError) as e:
            last = f"{type(e).__name__}: {e}"
            if attempt + 1 < retries:
                time.sleep(backoff_seconds(e, attempt))
    return False, last


def _download_any(urls: list[str], dest: Path, limiter: RateLimiter) -> tuple[bool, str]:
    errs = []
    for u in urls:
        ok, err = _download(u, dest, limiter)
        if ok:
            _remember_shape(u)
            return True, f"{err} <- {u}"
        errs.append(err)
    return False, "; ".join(errs)[:200]


def _remember_shape(url: str) -> None:
    """把刚成功的写法提到最前，后续请求直接命中。"""
    shape = None
    if "/pdf/" in url:
        if "export.arxiv.org" in url:
            shape = "export_versioned" if re.search(r"v\d+$", url) else "export_base"
        elif url.endswith(".pdf"):
            shape = "www_suffixed"
        else:
            shape = "www_base"
    if shape and _PREFERRED_SHAPE and _PREFERRED_SHAPE[0] != shape:
        _PREFERRED_SHAPE.remove(shape)
        _PREFERRED_SHAPE.insert(0, shape)


def attach_fulltext(papers: list[Paper], cache: Path | None = None,
                    limiter: RateLimiter | None = None,
                    verbose: bool = True) -> dict:
    """给 papers 列表补 fulltext。返回统计。

    只抓 arXiv 有 PDF 的条目；已缓存的不重复下载。
    解析失败**不回退到空串**，而是把 fulltext 留 None —— 匹配器会把这类规则
    标为「无法判定」，不会误判成「确认缺失」（见 D-REP-001 教训）。

    统计里区分 `failed` 与 `throttled`：前者是这条论文真有问题，
    后者是**我们**被限流了。混在一起会让人以为「arXiv 上没有这些 PDF」，
    那是个错误的结论，会直接把语料规模低估掉。
    """
    cache = cache or default_cache()
    limiter = limiter or RateLimiter(BULK_INTERVAL)
    stat = {"total": len(papers), "cached": 0, "downloaded": 0,
            "parsed": 0, "failed": 0, "throttled": 0, "no_pdf": 0}

    for i, p in enumerate(papers, 1):
        if p.fulltext:
            stat["parsed"] += 1
            continue
        if not p.id.startswith("arXiv") and "/" not in p.id and "." not in p.id:
            stat["no_pdf"] += 1
            continue
        dest = _pdf_path(cache, p.id)
        if dest.exists() and dest.stat().st_size > 5000:
            stat["cached"] += 1
        else:
            urls = _candidate_urls(p.id)
            if p.pdf_url:
                urls.insert(0, p.pdf_url)
            ok, err = _download_any(urls, dest, limiter)
            if not ok:
                stat["throttled"] += 1
                if verbose:
                    print(f"  [pdf {i}/{len(papers)}] 未取得 {p.id}: {err}")
                continue
            stat["downloaded"] += 1
            if verbose:
                print(f"  [pdf {i}/{len(papers)}] 已获取 {p.id}")
        text, backend = parse_pdf(dest)
        if text:
            p.fulltext = text
            p.source = p.source + "+pdf"
            stat["parsed"] += 1
            if verbose:
                print(f"      解析成功（{backend}，{len(text)} 字符）")
        else:
            stat["failed"] += 1
            if verbose:
                print(f"      解析失败（{backend}）")
    return stat


def corpus_fingerprint(papers: list[Paper]) -> str:
    """语料指纹：用于把「这次分析基于哪批全文」固定下来，便于复核。"""
    ids = sorted(p.id for p in papers)
    h = hashlib.sha256("|".join(ids).encode()).hexdigest()[:16]
    n_ft = sum(1 for p in papers if p.fulltext)
    return f"sha256:{h}  papers={len(ids)}  with_fulltext={n_ft}"


def fetch_query(query: str, target: int, limiter: RateLimiter | None = None) -> list[Paper]:
    from .arxiv import collect
    return collect(query, target=target, limiter=limiter)


def _quote(q: str) -> str:
    return urllib.parse.quote(q, safe="")


# ---------------------------------------------------------- manifest 语料
def _resolve_cache_dir(raw: str | Path | None, manifest_path: Path) -> Path:
    """缓存目录的解析锚点（对 cwd 无关，H1 契约）。

    旧实现等价于 `Path(raw or default_cache())`，全部相对**当前工作目录**解析
    —— 宿主 harness 从任意 cwd 调 CLI 时（这正是 H1 承诺的场景），相对路径
    静默落空，240 篇全文全部变「无缓存」，且没有任何报错
    （真实测试 2026-10-02 抓到，且 corpus_bulk.json 压根没写 cache_dir 字段）。

    解析顺序：
    * 显式绝对路径 → 原样返回；
    * manifest 显式给了相对 cache_dir → 依次尝试 manifest 所在目录、其父目录、
      父目录的父目录（v0.4.0 起 manifest 随包在 <repo>/fieldmate/libraries/，
      缓存仍在 <repo>/.fieldmate-cache，需要向上三层；editable 安装成立，
      wheel 安装时三层都在 site-packages 内、不存在，自然落到 cwd 兜底），
      取第一个真实存在的；都不存在则原样返回（「先建 manifest 后建缓存」
      的用法仍可用）；
    * manifest 没给 cache_dir → 先试 cwd 下的默认名（旧用法：在哪儿跑就在哪儿
      建缓存），再按 manifest 位置向上找已有缓存（离线复用），最后退回 cwd 相对。
    """
    mp = manifest_path.resolve().parent
    bases = (mp, mp.parent, mp.parent.parent)
    if raw is not None:
        c = Path(raw)
        if c.is_absolute():
            return c
        for base in bases:
            if (base / c).exists():
                return base / c
        return c
    c = default_cache()
    if c.exists():
        return c
    for base in bases:
        if (base / c).exists():
            return base / c
    return c


def load_manifest(path: str | Path, cache: Path | None = None,
                  verbose: bool = False) -> tuple[list[Paper], dict[str, Any]]:
    """从检索 manifest（corpus_bulk.json / corpus_arxiv.json）装载语料。

    与 `attach_fulltext` 的分工：**只读，不下载**。下载是限速且有副作用的网络行为，
    必须由调用方显式发起；装载应该是廉价的纯本地操作，可以随便重复调用。
    这样「跑一次分析」不会偷偷消耗配额，也不会因为网络抖动让分析结果不可复现。

    关键纪律
    --------
    缓存里**没有**全文的条目，`fulltext` 保持 `None` 而不是 `""`：
    前者让匹配器判「无法判定」（UNTESTED），后者会被当成「这篇论文里确实没有这段」
    ——那是 D-REP-001 那个假阳性事故的配方。空串在语义上是「我看过了，确实没有」，
    只有真的解析过才能这么说。

    返回 (papers, stats)。
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"manifest 不存在：{p}")
    mf = json.loads(p.read_text(encoding="utf-8"))
    entries = mf.get("papers") or []
    cache = cache if cache is not None else _resolve_cache_dir(mf.get("cache_dir"), p)

    papers: list[Paper] = []
    n_ft = n_stale = n_none = 0
    for e in entries:
        aid = str(e.get("id", "")).strip()
        if not aid:
            continue
        paper = Paper(
            id=aid,
            title=e.get("title", ""),
            abstract=e.get("abstract", ""),
            published=e.get("published", ""),
            categories=list(e.get("categories") or []),
        )
        dest = _pdf_path(cache, aid)
        if dest.exists() and dest.stat().st_size > 5000:
            text, backend = parse_pdf_cached(dest)
            if text:
                paper.fulltext = text
                paper.source = paper.source + "+pdf"
                n_ft += 1
            else:
                n_stale += 1          # 文件在但解析不出来：算「有但不可用」
        else:
            n_none += 1
        papers.append(paper)

    stats = {
        "manifest": str(p),
        "n_entries": len(entries),
        "n_loaded": len(papers),
        "n_fulltext": n_ft,
        "n_unparsable": n_stale,      # 有文件但解析失败
        "n_no_cache": n_none,         # 缓存里没有这个文件
        "cache_dir": str(cache),
        "queries": mf.get("queries", []),
    }
    if verbose:
        print(f"[corpus] {p}：{len(papers)} 条，有全文 {n_ft}，"
              f"无缓存 {n_none}，不可解析 {n_stale}", file=sys.stderr)
    return papers, stats
