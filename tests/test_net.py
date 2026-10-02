"""测试：网络层的退避与节流纪律。

为什么单独成文件
----------------
`test_core.py` 守的是**判断**的正确性（三态判定、最近邻、规则体检）。
这组守的是**抓取**的纪律：限流不是网络抖动，处理错了会让批量语料静默缩水，
而缩水后的语料会得出「这个领域没人报告 X」这种**看起来正常、实际是假的**结论。

实测代价：连跑两轮完整检索 + 下载必撞 429；而原先的 2s×attempt 通用重试
在 429 上完全无效（arXiv 的限流窗口是分钟级），于是一批论文被批量标成
「下载失败」——看起来像 arXiv 上没有这些 PDF，实际是我们自己被封了。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
from pathlib import Path

import pytest

from rharness.sources.arxiv import (
    MIN_INTERVAL,
    BULK_INTERVAL,
    RateLimiter,
    backoff_seconds,
    is_throttle,
)
from rharness.sources.fulltext import (
    _candidate_urls,
    _download,
    _earlier_versions,
    _looks_complete_pdf,
    _pdf_path,
    load_manifest,
)


def _http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError("https://arxiv.org/x", code, "msg", headers, None)


# ---------------------------------------------------- 限流判定
@pytest.mark.parametrize("code", [429, 503])
def test_throttle_codes_recognised(code):
    assert is_throttle(_http_error(code)) is True


@pytest.mark.parametrize("code", [400, 403, 404, 406, 500])
def test_non_throttle_codes_not_flagged(code):
    """404/406 必须**不**走长退避——那会让「确实没这篇」睡掉好几分钟。"""
    assert is_throttle(_http_error(code)) is False


def test_non_http_exception_not_flagged():
    assert is_throttle(TimeoutError()) is False
    assert is_throttle(OSError("connection reset")) is False


# ---------------------------------------------------- 退避阶梯
def test_429_uses_long_backoff_ladder():
    """429 的退避必须远大于普通错误，且单调递增。"""
    ladder = [backoff_seconds(_http_error(429), a) for a in range(4)]
    assert ladder == [30.0, 60.0, 120.0, 240.0]
    assert ladder == sorted(ladder), "退避阶梯必须递增，否则重试没有意义"


def test_ladder_is_clamped_not_unbounded():
    """再多次重试也不应超过阶梯末端，否则脚本可能睡到天亮。"""
    assert backoff_seconds(_http_error(429), 99) == 240.0


def test_ordinary_error_uses_short_backoff():
    """超时/连接重置这类不需要长等。"""
    assert backoff_seconds(TimeoutError(), 0) == 2.0
    assert backoff_seconds(_http_error(500), 1) == 4.0
    assert backoff_seconds(TimeoutError(), 99) == 200.0


def test_retry_after_header_is_honoured():
    """服务端明确说了等多久，就该照做——这是它表达的限流意图。"""
    assert backoff_seconds(_http_error(429, "300"), 0) == 300.0


def test_retry_after_raises_but_never_lowers_the_floor():
    """比阶梯小的时候不能把等待拉短，否则等于无视限流。"""
    assert backoff_seconds(_http_error(429, "5"), 2) == 120.0


def test_absurd_retry_after_is_ignored():
    """畸形 header 不能让脚本睡一整天——退回阶梯，不报错也不采信。"""
    assert backoff_seconds(_http_error(429, "99999"), 0) == 30.0
    assert backoff_seconds(_http_error(429, "-60"), 0) == 30.0


def test_garbage_retry_after_falls_back_to_ladder():
    assert backoff_seconds(_http_error(429, "soon"), 0) == 30.0
    assert backoff_seconds(_http_error(429, ""), 0) == 30.0


def test_error_without_headers_object_still_backs_off():
    """有些 HTTPError 实现没有 headers，退避必须仍能算出来（不能抛异常）。"""
    e = urllib.error.HTTPError("https://arxiv.org/x", 429, "msg", None, None)
    assert backoff_seconds(e, 1) == 60.0


# ---------------------------------------------------- 节流
def test_official_minimum_interval_is_respected():
    assert MIN_INTERVAL >= 3.0, "arXiv 官方要求请求间隔 >= 3 秒"


def test_bulk_interval_is_more_conservative_than_official_minimum():
    """批量下载必须比官方下限更保守——实测 3s 在几十篇规模上必撞 429。"""
    assert BULK_INTERVAL > MIN_INTERVAL


def test_rate_limiter_enforces_interval():
    lim = RateLimiter(0.30)
    lim.wait()
    t0 = time.time()
    lim.wait()
    assert time.time() - t0 >= 0.25, "限速器没有真正等待"


def test_rate_limiter_first_call_does_not_sleep():
    """会话刚开始时不该白等一个间隔。"""
    lim = RateLimiter(5.0)
    t0 = time.time()
    lim.wait()
    assert time.time() - t0 < 1.0


# ---------------------------------------------------- manifest 装载
def _manifest(tmp: Path, entries: list[dict]) -> Path:
    p = tmp / "m.json"
    p.write_text(json.dumps({"n_papers": len(entries), "papers": entries},
                            ensure_ascii=False), encoding="utf-8")
    return p


def test_load_manifest_reports_missing_fulltext_as_none_not_empty(tmp_path):
    """核心纪律：缓存里没有全文 -> fulltext 必须是 None，不能是空串。

    空串在语义上等于「我看过这篇，确实没有这段」，会让匹配器判成「确认缺失」，
    也就是 D-REP-001 的假阳性配方。这里锁死它。
    """
    mf = _manifest(tmp_path, [{"id": "2501.00001", "title": "t"}])
    papers, st = load_manifest(mf, cache=tmp_path / "cache")
    assert len(papers) == 1
    assert papers[0].fulltext is None, "没有全文必须是 None"
    assert papers[0].fulltext != "", "绝不能退化成空串"
    assert st["n_no_cache"] == 1
    assert st["n_fulltext"] == 0


def test_load_manifest_counts_categories_are_disjoint_and_complete(tmp_path):
    """n_fulltext + n_no_cache + n_unparsable 必须等于装载条数。"""
    mf = _manifest(tmp_path, [{"id": f"2501.0000{i}"} for i in range(5)])
    papers, st = load_manifest(mf, cache=tmp_path / "cache")
    assert st["n_loaded"] == len(papers) == 5
    assert (st["n_fulltext"] + st["n_no_cache"] + st["n_unparsable"]) == len(papers)


def test_load_manifest_unparsable_file_is_not_silently_empty(tmp_path):
    """文件够大但解析不出来 -> 归入 n_unparsable，fulltext 仍然是 None。

    不能因为「有文件」就当成有证据，也不能因为解析失败就记成空文本。
    特意写成 >5000B：小于该阈值的是上次中断留下的半截文件，
    那是另一类问题（n_no_cache），两者必须分开记账。
    """
    cache = tmp_path / "cache"
    cache.mkdir()
    # 体积达标、但内容不是 PDF
    (cache / "2501.00002.pdf").write_bytes(b"NOT-A-REAL-PDF " * 800)
    assert (cache / "2501.00002.pdf").stat().st_size > 5000
    mf = _manifest(tmp_path, [{"id": "2501.00002"}])
    papers, st = load_manifest(mf, cache=cache)
    assert st["n_unparsable"] == 1, "解析失败必须被单独记账"
    assert st["n_no_cache"] == 0, "够大的文件不算「没缓存」"
    assert papers[0].fulltext is None


def test_load_manifest_tiny_file_counts_as_no_cache(tmp_path):
    """小于阈值的残片不算缓存命中（多半是上次中断留下的半截文件）。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "2501.00003.pdf").write_bytes(b"%PDF-1.4 truncated")
    mf = _manifest(tmp_path, [{"id": "2501.00003"}])
    _, st = load_manifest(mf, cache=cache)
    assert st["n_no_cache"] == 1
    assert st["n_fulltext"] == 0


def test_load_manifest_missing_file_raises_loudly(tmp_path):
    """manifest 不存在必须显式报错，不能静默返回空语料。"""
    with pytest.raises(FileNotFoundError):
        load_manifest(tmp_path / "nope.json", cache=tmp_path)


def test_load_manifest_skips_blank_ids(tmp_path):
    mf = _manifest(tmp_path, [{"id": ""}, {"id": "   "}, {"id": "2501.00004"}])
    papers, _ = load_manifest(mf, cache=tmp_path / "cache")
    assert [p.id for p in papers] == ["2501.00004"]


def test_pdf_path_strips_version(tmp_path):
    """v1/v2 是同一篇，不能各存一份浪费空间和触发重复下载。"""
    assert _pdf_path(tmp_path, "2501.00005v3").name == "2501.00005.pdf"
    assert _pdf_path(tmp_path, "2501.00005v1").name == "2501.00005.pdf"


def test_candidate_urls_cover_bare_and_suffixed_forms():
    """406 只出现在部分写法上，多候选是实测出来的必要兜底，不是冗余。"""
    urls = _candidate_urls("2501.00006")
    assert any("export.arxiv.org" in u for u in urls)
    assert any(u.endswith(".pdf") for u in urls)
    assert len(urls) >= 2


def test_export_host_is_tried_first():
    """实测对拍：export.arxiv.org 三篇全中，www 的 .pdf 形式三篇全灭。

    这条把实测结论钉死，避免以后有人按「示例代码的惯例」把
    `arxiv.org/pdf/{id}.pdf` 换回首选项 —— 那会让大批论文重新变成「取不到」。
    """
    urls = _candidate_urls("2501.00006")
    assert "export.arxiv.org" in urls[0], "export 主机必须是首选"


def test_www_suffixed_form_is_not_first():
    """.pdf 后缀形式实测 0/3，不能排在第一位。"""
    urls = _candidate_urls("2501.00006")
    assert not urls[0].endswith(".pdf")


def test_successful_url_shape_is_learned_not_hardcoded():
    """成功的写法要被提到最前 —— 否则每篇都先白烧一轮失败重试。

    这不是洁癖：固定顺序把最容易失败的写法排在第一位，实测会让
    单篇耗时从 ~8s 涨到 ~85s，且请求数翻数倍、限流暴露面同步放大。
    """
    import rharness.sources.fulltext as ft
    saved = list(ft._PREFERRED_SHAPE)
    try:
        ft._PREFERRED_SHAPE[:] = list(ft._URL_SHAPES)
        ft._remember_shape("http://export.arxiv.org/pdf/2501.00007")
        assert ft._PREFERRED_SHAPE[0] == "export_base"
        ft._remember_shape("https://arxiv.org/pdf/2501.00007.pdf")
        assert ft._PREFERRED_SHAPE[0] == "www_suffixed", "suffixed 成功后应排到最前"
        ft._remember_shape("https://arxiv.org/pdf/2501.00007")
        assert ft._PREFERRED_SHAPE[0] == "www_base", "www bare 成功后应排到最前"
        assert sorted(ft._PREFERRED_SHAPE) == sorted(ft._URL_SHAPES), \
            "学习顺序不能把候选写丢掉"
    finally:
        ft._PREFERRED_SHAPE[:] = saved


def test_remember_shape_does_not_mistake_version_for_suffix():
    """带版本号的 export 地址要认成 export_versioned，不能误判成别的形状。"""
    import rharness.sources.fulltext as ft
    saved = list(ft._PREFERRED_SHAPE)
    try:
        ft._PREFERRED_SHAPE[:] = list(ft._URL_SHAPES)
        ft._remember_shape("http://export.arxiv.org/pdf/2501.00008v2")
        assert ft._PREFERRED_SHAPE[0] == "export_versioned"
    finally:
        ft._PREFERRED_SHAPE[:] = saved


def test_learned_order_still_covers_all_shapes_without_duplicates():
    """学习只改顺序不减候选，但候选内部要去重。

    不带版本号的 id 下 base 与 versioned 会落到同一个地址，
    留着重复项等于在失败时白烧一轮重试。

    断言的是「四种写法一个不少 + 无重复」，**不是候选总数**：
    候选表还包含更早版本号的回退（见 2609.24871v2 的实测），
    总数会随版本号变化，把总数钉死会让这个测试在正确扩展时误报。
    """
    import rharness.sources.fulltext as ft
    saved = list(ft._PREFERRED_SHAPE)
    try:
        plain = _candidate_urls("2501.00008")
        assert len(plain) == len(set(plain)), "候选内不能有重复地址"
        versioned = _candidate_urls("2501.00008v2")
        assert len(versioned) == len(set(versioned)), "候选内不能有重复地址"
        for shape in ft._URL_SHAPES:
            assert ft._url_for(shape, "2501.00008v2", "2501.00008") in versioned, \
                f"学习过顺序后 {shape} 写法仍必须在候选里"
    finally:
        ft._PREFERRED_SHAPE[:] = saved


# ---------------------------------------------------- 解析缓存的失效纪律
def test_parsed_text_cache_is_used_on_second_read(tmp_path, monkeypatch):
    """同一份 PDF 第二次读必须走缓存 —— 240 篇重解析要 2.5 分钟，这是痛点本身。"""
    import rharness.sources.local as loc
    monkeypatch.setattr(loc, "_text_cache_dir", lambda: tmp_path / "tc")
    src = tmp_path / "a.pdf"
    src.write_bytes(b"%PDF-1.4 hello world " * 100)

    calls = {"n": 0}

    def fake(p, max_chars=2_000_000):
        calls["n"] += 1
        return "extracted text", "fake"

    monkeypatch.setattr(loc, "parse_pdf", fake)
    t1, b1 = loc.parse_pdf_cached(src)
    t2, b2 = loc.parse_pdf_cached(src)
    assert t1 == t2 == "extracted text"
    assert b1 == "fake" and b2 == "cache", "第二次必须命中缓存"
    assert calls["n"] == 1, "第二次不该再解析"


def test_parsed_text_cache_invalidates_when_pdf_changes(tmp_path, monkeypatch):
    """PDF 内容变了必须重解析。只按 size 判会漏掉「同尺寸换内容」，那会让结论悄悄错。"""
    import rharness.sources.local as loc
    monkeypatch.setattr(loc, "_text_cache_dir", lambda: tmp_path / "tc")
    src = tmp_path / "b.pdf"
    src.write_bytes(b"%PDF-1.4 v1 " * 100)

    seen = {"v": "first version"}

    def fake(p, max_chars=2_000_000):
        return seen["v"], "fake"

    monkeypatch.setattr(loc, "parse_pdf", fake)
    assert loc.parse_pdf_cached(src)[0] == "first version"

    seen["v"] = "second version"
    # 换内容并保证 mtime 也动
    src.write_bytes(b"%PDF-1.4 v2 " * 100)
    os.utime(src, (time.time() + 10, time.time() + 10))
    txt, backend = loc.parse_pdf_cached(src)
    assert txt == "second version", "PDF 变了还读旧缓存 = 结论悄悄错掉"
    assert backend == "fake", "变了就该重新解析"


def test_parsed_text_cache_key_includes_max_chars(tmp_path, monkeypatch):
    """max_chars 是硬上限，参与缓存键；否则调小上限后会读到被截断的旧文本。"""
    import rharness.sources.local as loc
    monkeypatch.setattr(loc, "_text_cache_dir", lambda: tmp_path / "tc")
    src = tmp_path / "c.pdf"
    src.write_bytes(b"%PDF-1.4 x " * 100)
    monkeypatch.setattr(loc, "parse_pdf",
                        lambda p, max_chars=2_000_000: ("A" * 50, "fake"))
    assert loc.parse_pdf_cached(src, max_chars=50)[0] == "A" * 50
    monkeypatch.setattr(loc, "parse_pdf",
                        lambda p, max_chars=2_000_000: ("B" * 10, "fake"))
    assert loc.parse_pdf_cached(src, max_chars=10)[0] == "B" * 10, "上限变了必须重解析"


def test_parsed_text_cache_write_failure_is_silent_but_read_failure_is_not(tmp_path, monkeypatch):
    """缓存写不进去要静默退回直解（缓存是优化不是正确性）；读不出来则必须重解。"""
    import rharness.sources.local as loc
    monkeypatch.setattr(loc, "_text_cache_dir", lambda: tmp_path / "ro")
    src = tmp_path / "d.pdf"
    src.write_bytes(b"%PDF-1.4 y " * 100)
    calls = {"n": 0}

    def fake(p, max_chars=2_000_000):
        calls["n"] += 1
        return "text here", "fake"

    monkeypatch.setattr(loc, "parse_pdf", fake)
    (tmp_path / "ro").mkdir()                      # 目录存在但不可写 -> 写失败
    (tmp_path / "ro" / "d.txt").write_text("corrupted", encoding="utf-8")
    (tmp_path / "ro" / "d.meta").write_text("wrong key", encoding="utf-8")
    txt, backend = loc.parse_pdf_cached(src)
    assert txt == "text here", "缓存对不上时必须重解，不能返回缓存里的残留"
    assert calls["n"] >= 1


def test_parsed_text_cache_returns_none_for_missing_file(tmp_path):
    """文件不存在时不能抛异常之外的怪错，也不能凭空造出文本。"""
    import rharness.sources.local as loc
    txt, backend = loc.parse_pdf_cached(tmp_path / "ghost.pdf")
    assert txt is None, "不存在的文件绝不能返回文本"


# ---------------------------------------------------- 截断文件不得冒充成品
# 实测代价：429 风暴期间抓到 6 份被截断的 PDF，全部头部 %PDF 正常、
# 尾部无 %%EOF、pypdf 报 Stream has ended unexpectedly、fitz 读到 0 页。
# 早先 _download 把 >5000B 的 IncompleteRead 残片直接写进最终文件并返回成功，
# 于是「文件存在且 >5000B」= 已缓存 -> 这些条目**永远不会被重下**，
# 语料里留下永久读不出正文的僵尸条目。
#
# 这组测试守的是「缓存不会被静默污染」——污染的缓存比缺缓存更危险，
# 因为它让缺失**看不出来**。


def _pdf_bytes(with_eof: bool = True, body: int = 6000) -> bytes:
    head = b"%PDF-1.5\n"
    pad = b"x" * body
    return head + pad + (b"\n%%EOF\n" if with_eof else b"\n\x01\x02\x03")


def test_complete_pdf_is_recognised():
    assert _looks_complete_pdf(_pdf_bytes(True)) is True


def test_truncated_pdf_is_rejected():
    """尾部没有 %%EOF 的就是没下完，必须判为不完整。"""
    assert _looks_complete_pdf(_pdf_bytes(False)) is False


def test_error_page_is_not_a_pdf():
    """HTML 错误页即使超过 5000 字节也不该被当成 PDF 收下。"""
    assert _looks_complete_pdf(b"<html>" + b"y" * 9000) is False


def test_empty_payload_is_rejected():
    assert _looks_complete_pdf(b"") is False


def test_eof_far_from_end_is_still_truncated():
    """%%EOF 被埋在中间、后面还堆着 5000 字节垃圾 -> 不是一份干净的 PDF。

    这正是「拼接了两个响应体」或「残片后面又接了别的」时的形态。
    """
    data = b"%PDF-1.5\n" + b"x" * 9000 + b"%%EOF\n" + b"y" * 5000
    assert _looks_complete_pdf(data) is False


def test_eof_inside_window_with_trailing_blank_is_accepted():
    """规范允许 %%EOF 后跟少量空白，所以不能要求精确等于文件末尾。"""
    data = _pdf_bytes(True) + b"\r\n" * 64
    assert _looks_complete_pdf(data) is True


def test_incomplete_read_partial_never_written_as_final_pdf(tmp_path, monkeypatch):
    """核心回归：残片只能进 .part，成品 .pdf 必须不被创建。

    这正是那 6 份僵尸条目的成因。
    """
    import http.client

    dest = tmp_path / "x.pdf"

    def _boom(*a, **k):
        raise http.client.IncompleteRead(b"%PDF-1.5\n" + b"x" * 9000)

    monkeypatch.setattr("urllib.request.urlopen", _boom)
    ok, err = _download("http://example/x", dest, RateLimiter(0), retries=1)

    assert ok is False, "截断的响应不能算下载成功"
    assert not dest.exists(), (
        f"截断残片绝不能落到 {dest.name}，否则下次运行会当它已缓存而永久跳过")
    assert "IncompleteRead" in err
    assert dest.with_suffix(".pdf.part").exists(), "残片应留存到 .part 供下轮重下"


def test_complete_response_is_written_and_part_cleared(tmp_path, monkeypatch):
    """正常路径仍要落 .pdf，并清掉遗留的 .part。"""
    dest = tmp_path / "y.pdf"
    dest.with_suffix(".pdf.part").write_bytes(b"stale")

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return _pdf_bytes(True)

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: _Resp())
    ok, _ = _download("http://example/y", dest, RateLimiter(0), retries=1)

    assert ok is True
    assert dest.exists() and dest.stat().st_size > 5000
    assert not dest.with_suffix(".pdf.part").exists(), "成功后不该留下 .part"


# ---------------------------------------------------- 版本回退
# 实测：2609.24871v2 在 export 上 v2 与无版本号双双 404，**只有 v1 拿得到**
# （10.6 MB，完整）。「取最新版本」这个默认假设并不总成立。


def test_earlier_versions_descend_from_the_given_one():
    assert _earlier_versions("2609.24871v4") == [
        "2609.24871v3", "2609.24871v2", "2609.24871v1"]


def test_earlier_versions_stops_at_v1():
    assert _earlier_versions("2609.24871v2") == ["2609.24871v1"]
    assert _earlier_versions("2609.24871v1") == []


def test_earlier_versions_empty_when_id_has_no_version():
    assert _earlier_versions("2609.24871") == []


def test_earlier_versions_are_bounded():
    """v99 不该生成 98 个候选 URL —— 那会把限流暴露面放大到不可接受。"""
    assert len(_earlier_versions("2609.24871v99")) == 3


def test_candidate_urls_include_earlier_version_as_last_resort():
    urls = _candidate_urls("2609.24871v2")
    assert "http://export.arxiv.org/pdf/2609.24871v1" in urls
    assert urls[-1].endswith("2609.24871v1"), "版本回退必须是最后一招，不抢前面写法的位置"


def test_candidate_urls_have_no_duplicate_version_fallbacks():
    urls = _candidate_urls("2609.24871v2")
    assert len(urls) == len(set(urls))
