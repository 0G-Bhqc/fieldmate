"""arXiv 检索源（Atom API，纯 stdlib）

设计要点
--------
* **只用 stdlib**：不引入 requests/feedparser，保持「装完就能跑、挂任何 harness 都能用」。
* **限速是硬要求**：arXiv 官方要求每次请求间隔 ≥3 秒。少一条限速就会
  在批量扫描时被封，而批量扫描正是本框架的主用途。这里做成会话级节流。
* **query 必须落盘**：检索式是结论可复核的前提（风险 R7）。返回结果里始终带 query 原文。

已实测可用（2026-10）：all:"mesh denoising" → 25 篇；all:"phase field" AND all:denoising → 7 篇。
"""

from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Any

API = "http://export.arxiv.org/api/query"
NS = {
    "a": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
    "os": "http://a9.com/-/spec/opensearch/1.1/",
}
MIN_INTERVAL = 3.0          # arXiv 官方要求：两次请求至少间隔 3 秒
# 批量下载用的间隔。定 5s 而不是 3s，是因为「命中即返回」并不总是成立：
# 每篇仍有小概率撞上 406/IncompleteRead 而要试下一个候选地址，留一点余量。
# 真正把限流暴露面压下去的是「每篇只发一次请求」（候选地址按实测成功率排序），
# 那比调间隔有效得多。
BULK_INTERVAL = 5.0
_UA = "fieldmate/0.2 (fieldmate; +https://github.com/your-org/fieldmate)"


@dataclass
class Paper:
    """一篇论文的元数据（arXiv 可得的部分）。"""

    id: str
    title: str
    abstract: str
    authors: list[str] = field(default_factory=list)
    published: str = ""          # ISO date
    updated: str = ""
    categories: list[str] = field(default_factory=list)
    doi: str | None = None
    journal_ref: str | None = None
    pdf_url: str | None = None
    abs_url: str = ""
    source: str = "arxiv"
    fulltext: str | None = None  # 由 local/pdf 解析后回填

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def year(self) -> int | None:
        return int(self.published[:4]) if self.published[:4].isdigit() else None

    @property
    def venue(self) -> str:
        """优先 journal_ref（说明已被期刊接收），否则退到主分类。"""
        if self.journal_ref:
            return self.journal_ref.strip()
        return self.categories[0] if self.categories else "arXiv"

    def text_for(self, scope: str) -> str:
        """按 scope 取待检索文本。"""
        if scope == "title":
            return self.title
        if scope == "abstract":
            return self.abstract
        if scope == "fulltext":
            return self.fulltext or ""
        return f"{self.title}\n{self.abstract}\n{self.fulltext or ''}"


class RateLimiter:
    """会话级限速器（arXiv 要求 ≥3s/请求）。

    `interval` 可调高用于批量下载：官方 3s 是**检索 API** 的下限，
    但 PDF 下载走的是另一条路径（CDN 边缘节点），实测批量下载时
    3s/请求仍会撞 429。批量场景请传 6~10（见 `attach_fulltext`）。
    """

    def __init__(self, interval: float = MIN_INTERVAL):
        self.interval = interval
        self._last = 0.0

    def wait(self) -> None:
        dt = time.time() - self._last
        if dt < self.interval:
            time.sleep(self.interval - dt)
        self._last = time.time()


# 429/503 的退避阶梯（秒）。实测连跑两轮完整检索 + 下载必撞 429，
# 而 2s×attempt 的通用重试在 429 上**完全无效** —— arXiv 的限流窗口是分钟级，
# 不是秒级。所以这两种状态码必须走独立的、长得多的退避。
_THROTTLE_BACKOFF = (30.0, 60.0, 120.0, 240.0)
_THROTTLE_CODES = {429, 503}


def is_throttle(e: BaseException) -> bool:
    """是否属于「被限流 / 服务暂时不可用」，需要长退避。"""
    return isinstance(e, urllib.error.HTTPError) and e.code in _THROTTLE_CODES


# 旧内部名保留：模块内早期调用点仍在用
_is_throttle = is_throttle


def backoff_seconds(e: BaseException, attempt: int) -> float:
    """按异常类型决定退避时长。

    * 429/503：走长阶梯，并优先采用服务端给的 `Retry-After`（若在合理范围内）。
      这是 arXiv 明确表达的等待意图，忽略它就是无视对方的限流信号。
    * 其他（超时、连接重置）：短阶梯，2s×attempt 足够。
    """
    if is_throttle(e):
        n = len(_THROTTLE_BACKOFF)
        base = _THROTTLE_BACKOFF[min(attempt, n - 1)]
        ra = None
        headers = getattr(e, "headers", None)
        if headers is not None:
            try:
                ra = headers.get("Retry-After")
            except Exception:      # headers 实现各异，取不到就退回阶梯值
                ra = None
        if ra:
            try:
                val = float(str(ra).strip())
                # 只信任 0~600s 的值；异常的 header 不该让脚本睡一整天
                if 0 <= val <= 600:
                    base = max(base, val)
            except ValueError:
                pass
        return base
    return 2.0 * (attempt + 1)


def _fetch(url: str, limiter: RateLimiter, retries: int = 3, timeout: int = 45) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        limiter.wait()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
            last = e
            if attempt + 1 < retries:
                time.sleep(backoff_seconds(e, attempt))
    raise RuntimeError(f"arXiv 请求失败（重试 {retries} 次）：{last}")


def _parse_entry(el: ET.Element) -> Paper | None:
    def txt(path: str, default: str = "") -> str:
        node = el.find(path, NS)
        return (re.sub(r"\s+", " ", node.text).strip()
                if (node is not None and node.text) else default)

    raw_id = txt("a:id")
    if not raw_id:
        return None
    m = re.search(r"abs/([\w.\-/]+)v(\d+)", raw_id)
    arx_id, ver = (m.group(1), m.group(2)) if m else (raw_id, "")
    links = {}
    for ln in el.findall("a:link", NS):
        links[ln.get("title", "")] = ln.get("href", "")
    doi = el.findtext("arxiv:doi", "", NS) or None
    jref = el.findtext("arxiv:journal_ref", "", NS) or None
    cats = [c.get("term", "") for c in el.findall("a:category", NS)]
    return Paper(
        id=f"{arx_id}v{ver}" if ver else arx_id,
        title=txt("a:title"),
        abstract=txt("a:summary"),
        authors=[re.sub(r"\s+", " ", a.findtext("a:name", "", NS)).strip()
                 for a in el.findall("a:author", NS)],
        published=txt("a:published")[:10],
        updated=txt("a:updated")[:10],
        categories=cats,
        doi=doi or None,
        journal_ref=jref or None,
        pdf_url=links.get("pdf"),
        abs_url=f"https://arxiv.org/abs/{arx_id}",
    )


def search(query: str, max_results: int = 50, start: int = 0,
           sort_by: str = "submittedDate", sort_order: str = "descending",
           limiter: RateLimiter | None = None) -> dict[str, Any]:
    """检索 arXiv。

    返回 {"query":..., "total":int, "papers":[Paper,...], "retrieved":int}
    —— query 原文一并返回，供报告落盘（风险 R7）。
    """
    limiter = limiter or RateLimiter()
    per_page = min(100, max(1, max_results - start))
    # quote_via=quote 很关键：默认 urlencode 会把 "+" 编成 %2B，把空格编成 "+"，
    # 于是用户按 arXiv 习惯写的 all:phase+field 会被当成字面加号而搜不到。
    # 用 quote 后空格->%20、加号保持字面，arXiv 两种写法都认。
    url = API + "?" + urllib.parse.urlencode(
        {"search_query": query, "start": start, "max_results": per_page,
         "sortBy": sort_by, "sortOrder": sort_order},
        quote_via=urllib.parse.quote)
    root = ET.fromstring(_fetch(url, limiter))
    total_el = root.find("os:totalResults", NS)
    total = int(total_el.text) if total_el is not None and total_el.text else 0
    papers = [p for p in (_parse_entry(e) for e in root.findall("a:entry", NS)) if p]
    return {"query": query, "total": total, "papers": papers,
            "retrieved": len(papers)}


def collect(query: str, target: int = 50, limiter: RateLimiter | None = None,
            **kw) -> list[Paper]:
    """分页取到 target 篇为止，自动去重（arXiv 的版本号会让同文重复）。"""
    limiter = limiter or RateLimiter()
    out: dict[str, Paper] = {}
    start = 0
    while len(out) < target:
        page = search(query, max_results=min(100, target - len(out)),
                      start=start, limiter=limiter, **kw)
        if not page["papers"]:
            break
        for p in page["papers"]:
            out.setdefault(p.id.split("v")[0], p)
        start += len(page["papers"])
        if start >= page["total"]:
            break
    return list(out.values())[:target]
