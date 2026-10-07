"""本地 PDF / 目录数据源

解析后端优先级：pypdf → pdfminer → fitz(PyMuPDF) → 纯失败。
三者都不是硬依赖：缺任何一个只是换一种解析器，全缺则明确报错（不静默返回空文本，
理由见缺陷库 D-REP-001）。

PDF → 文本的两类已知损失，在这里写清楚以便使用者判断信号可信度：
  1. **公式会被压成乱码或丢失**：本框架的 L1 信号不依赖公式细节，
     只依赖文字描述（如 "we report the resolution"），所以不受影响。
  2. **表格可能丢失**：若某篇把参数只放在表格里，L1/L2 都会漏判。
     这是本方法的**已知假阴性来源**，已写进报告的 limitations。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .arxiv import Paper

_EXTRACTORS: list[tuple[str, Any]] = []

# 是否注册 pymupdf4llm 结构后端。**默认 False** —— 零依赖是本项目的硬底线，
# 详见 `_register_extractors` 的 docstring 里的取舍说明。
# 改这个值（或调 enable_structured_backend()）之前请先读那段。
_USE_LLM_BACKEND = bool(__import__("os").environ.get("FIELDMATE_PYMUPDF4LLM"))


def enable_structured_backend(on: bool = True) -> None:
    """打开/关闭 pymupdf4llm 结构后端。必须在任何解析发生**之前**调用。

    打开后：
      * `parse_pdf` 优先返回 Markdown（标题带 `#`，是显式结构而非猜的）；
      * `fieldmate.extract.slots` 会直接认 Markdown 标题；
      * 参考文献区裁剪**不变**，仍走 `find_reference_start`。

    装不上 / 没装这个包时静默退回原有后端链，功能降级但不报错 ——
    可选依赖不该成为运行的前置条件。
    """
    global _USE_LLM_BACKEND
    if _USE_LLM_BACKEND == on:
        return
    _USE_LLM_BACKEND = on
    _EXTRACTORS.clear()          # 清空后下次 parse 会按新设置重新注册


def structured_backend_available() -> bool:
    """pymupdf4llm 是否可用（**不改变任何全局状态**）。"""
    if not _USE_LLM_BACKEND:
        return False
    try:
        import pymupdf4llm  # noqa: PLC0415, F401
        return True
    except ImportError:
        return False


def _register_extractors() -> None:
    """注册 PDF 解析后端。**顺序按稳健性排，不按可用性排。**

    实测：pypdf 遇到截断 PDF 会打一串 "EOF marker not found" 警告且很慢；
    fitz(PyMuPDF) 对同一文件直接给出文本。因此 fitz 优先，pypdf 次之，
    pdfminer 最后（最慢但对某些畸形文件更宽容）。

    pymupdf4llm 是**可选增强**，不是必需件
    ------------------------------------
    它给的是 Markdown 而不是裸文本，**章节标题是带 `#` 标记的显式结构**，
    而不是靠正则去猜行首。实测 20 篇 arXiv 全文：

        指标                       fitz 裸文本+正则    pymupdf4llm
        识别到的标题总数                85              296
        有 method 类标题的篇数      3/20 (15%)      13/20 (65%)
        能定位参考文献区的篇数       20/20            18/20
        每篇耗时                    ~0.2s            7.38s

    关键观察：**两者互补，不是替代**。它找结构强得多，但它慢 37 倍，
    而且在「裁参考文献区」这件事上**反而不如我的行首正则**（18/20 vs 20/20）——
    arXiv 的 PDF 常常根本没有可见的 References 标题，它拿不到。
    所以这里只接管「结构识别」，参考文献区裁剪仍走 `find_reference_start`。

    为什么默认不启用
    --------------
    1. 它把「纯 stdlib 即可跑」变成「有它更好」——这条底线不能破；
    2. 7.38s/篇 × 247 篇 ≈ 30 分钟，是一次性成本（结果落盘缓存）；
    3. 它偶尔会漏掉参考文献标题（第 3 点），会与我的裁剪逻辑打架。

    所以：**不装 = 行为完全不变，装了 = 结构识别变强**，由调用方显式选择。
    """
    if _EXTRACTORS:
        return
    # pymupdf4llm 只在显式要求时注册，**不参与默认竞争**：
    # 它慢 37 倍，作为默认后端会把「跑一次分析」从秒级拖到分钟级。
    if _USE_LLM_BACKEND:
        try:
            import pymupdf4llm  # noqa: PLC0415
            def _llm(path: Path) -> str:
                return pymupdf4llm.to_markdown(str(path))
            _EXTRACTORS.append(("pymupdf4llm", _llm))
        except ImportError:
            pass
    try:
        import fitz  # noqa: PLC0415
        # MuPDF 把语法错误直接写进 stdout/stderr。截断 PDF 上会刷几百行，
        # 足以淹没真正的报告输出。必须显式关掉——这是踩过的坑。
        try:
            fitz.TOOLS.mupdf_display_errors(False)
        except Exception:                                               # noqa: BLE001, S110
            pass
        def _fitz(path: Path) -> str:
            with fitz.open(str(path)) as doc:
                return "\n".join(pg.get_text() for pg in doc)
        _EXTRACTORS.append(("fitz", _fitz))
    except ImportError:
        pass
    try:
        import logging  # noqa: PLC0415

        from pypdf import PdfReader  # noqa: PLC0415
        logging.getLogger("pypdf").setLevel(logging.ERROR)              # 掐掉 EOF 警告
        def _pypdf(path: Path) -> str:
            rd = PdfReader(str(path))
            return "\n".join((pg.extract_text() or "") for pg in rd.pages)
        _EXTRACTORS.append(("pypdf", _pypdf))
    except ImportError:
        pass
    try:
        from pdfminer.high_level import extract_text  # noqa: PLC0415
        _EXTRACTORS.append(("pdfminer", lambda p: extract_text(str(p))))
    except ImportError:
        pass


def available_backends() -> list[str]:
    _register_extractors()
    return [name for name, _ in _EXTRACTORS]


def _sanitize_extracted_text(txt: str) -> str:
    """清掉 PDF 抽取产物中的孤立代理字符（\\udcXX）。

    pypdf/pdfminer 对某些字体与自 制 CMap 的抽取会留下这类字符，它们**无法用
    UTF-8 编码**：不消毒则解析缓存落盘、`--out` 的 JSON 落盘、任何严格编码的
    下游全部 UnicodeEncodeError（真实测试 2026-10-02：干净 venv 只装 pypdf
    首次解析 corpus_bulk 即崩；老环境没炸只是因为 fitz 的缓存早已存在）。
    语义 = 「该字符不可表示」，替换为 ?，与 CLI 输出流的 errors='replace' 约定一致。
    """
    try:
        txt.encode("utf-8")
    except UnicodeEncodeError:
        txt = txt.encode("utf-8", "replace").decode("utf-8")
    return txt


def parse_pdf(path: str | Path, max_chars: int = 2_000_000) -> tuple[str | None, str]:
    """返回 (text, backend)。解析失败返回 (None, 原因)。

    `max_chars` 是硬上限：有些 PDF 能解出几 MB 纯文本，全量喂给正则会
    显著拖慢匹配，而且后半部分（参考文献）几乎不贡献信号。
    """
    _register_extractors()
    if not _EXTRACTORS:
        return None, "没有任何 PDF 解析后端（pip install pypdf 或 pdfminer.six 或 pymupdf）"
    p = Path(path)
    errs = []
    for name, fn in _EXTRACTORS:
        try:
            txt = fn(p)
            if txt and len(txt) > max_chars:
                txt = txt[:max_chars]
            if txt and txt.strip():
                return _sanitize_extracted_text(txt), name
            errs.append(f"{name}:空文本")
        except Exception as e:                          # noqa: BLE001, PERF203
            errs.append(f"{name}:{type(e).__name__}")
    return None, "; ".join(errs)[:200]


# ---------------------------------------------------------- 解析结果缓存
# 实测：240 篇 PDF 每次重解析约 2.5 分钟。这让 `gaps --corpus` 之类的离线分析
# 变得不可用 —— 不是因为它慢，而是因为**慢的那部分与本次分析无关**。
# 语料一旦抓好就不变，重复解析纯属浪费。
#
# 失效键用 (大小, mtime) 而不只是大小：重新下载同一篇论文时字节数可能完全相同
# （服务器给的就是同一份文件），只按大小判会读到过期文本 —— 那会让结论悄悄错掉，
# 且极难发现。
_TXT_CACHE_DIRNAME = "parsed_text"


def _text_cache_dir() -> Path:
    import os
    env = os.environ.get("FIELDMATE_CACHE_DIR")
    if env:
        return Path(env) / _TXT_CACHE_DIRNAME
    cwd_cache = Path(".fieldmate-cache") / _TXT_CACHE_DIRNAME
    if cwd_cache.exists():
        return cwd_cache
    curr = Path.cwd().resolve()
    for parent in [curr] + list(curr.parents)[:4]:
        cand = parent / ".fieldmate-cache" / _TXT_CACHE_DIRNAME
        if cand.exists():
            return cand
        cand_tests = parent / "tests" / ".fieldmate-cache" / _TXT_CACHE_DIRNAME
        if cand_tests.exists():
            return cand_tests
    return cwd_cache


def parse_pdf_cached(path: str | Path, max_chars: int = 2_000_000) -> tuple[str | None, str]:
    """带磁盘缓存的 parse_pdf。缓存键含 size+mtime+**后端模式**，PDF/后端一变自动失效。

    缓存写入失败（只读目录 / 磁盘满）时**静默退回直解**，不报错：
    缓存是优化，不是正确性依赖。但读缓存失败必须重解，不能返回半截文本。

    为什么键里必须有后端模式
    ----------------------
    裸文本和 Markdown 是**两种不同的产物**，但缓存文件名只按 PDF 名字分。
    开着结构后端跑一次、再关掉跑一次，第二次会直接读到第一次的 Markdown，
    于是「关掉后端」看起来生效了（后端名变成 cache），实际文本还是带 `#` 的。
    这会让开关变成假开关，而且极难发现 —— 键里加上模式字符串就彻底避免。
    """
    _register_extractors()
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return parse_pdf(p, max_chars)

    mode = "md" if _USE_LLM_BACKEND else "txt"
    cdir = _text_cache_dir()
    cf = cdir / (p.stem + f".{mode}.txt")
    meta = cf.with_suffix(".meta")
    key = f"{st.st_size}\t{int(st.st_mtime)}\t{max_chars}\t{mode}"
    try:
        if cf.exists() and meta.exists() and meta.read_text(encoding="utf-8") == key:
            txt = cf.read_text(encoding="utf-8")
            if txt.strip():
                return txt, "cache"
    except OSError:
        pass

    txt, backend = parse_pdf(p, max_chars)
    if txt:
        try:
            cdir.mkdir(parents=True, exist_ok=True)
            cf.write_text(txt, encoding="utf-8")
            meta.write_text(key, encoding="utf-8")
        except OSError:
            pass          # 缓存写不进去不影响结果，只是下次慢一点
    return txt, backend


def _guess_title(text: str, stem: str) -> str:
    for line in text.splitlines():
        s = line.strip()
        if 12 <= len(s) <= 200 and not s.lower().startswith(("arxiv:", "http")):
            if sum(c.isalpha() for c in s) > 0.6 * len(s):
                return s
    return stem


# ------------------------------------------------------------ 参考文献区裁剪
#
# 为什么必须裁
# ------------
# `build_matrix` 判断「这篇论文报没报 Δt / 分辨率 / 稳定条件」时，
# 搜的是 `title + abstract + fulltext`，而 fulltext **含参考文献区**。
# 于是一篇论文只要**引用**了报 Δt 的工作，它自己就被判成「报了」。
#
# 实测偏差（247 篇全文，同一套 REPORT_ITEMS，剔除前后对比）：
#
#   报稳定条件   75% -> 79%   (+3.6pp)
#   报分辨率     69% -> 70%   (+1.2pp)
#   报噪声模型   66% -> 66%   (+0.8pp)
#   保体积/守恒  78% -> 79%   (+0.8pp)
#   报时间步     45% -> 45%   (+0.4pp)
#   开源自复现   96% -> 96%   (+0.4pp)
#
# 幅度不大（参考文献区中位在全文 94% 处，只占 ~6% 篇幅，而判据要求具体数值格式）。
# **但仍要修**，理由不是这 3.6pp，而是**口径一致性**：
# 247 篇里有 22 篇（9%）定位不到参考文献区标题，于是同一批语料里
# 一部分篇目按「含参考文献」测、一部分按「不含」测。测量口径不统一，
# 比统一的微小偏差更糟 —— 它让「这个数字是怎么来的」没法复现。
REF_HEADING = re.compile(
    r"^\s*(?:#{1,6}\s*)?"                          # 可选的 Markdown 标题标记
    r"(?:\d+(?:\.\d+)*\.?\s*|[IVXLC]+\.?\s*)?"
    r"\**\s*(?:references|bibliography|reference\s+list|literature\s+cited)\s*\**\s*"
    r"[:.]?\s*$",
    re.IGNORECASE)


def find_reference_start(text: str) -> int:
    r"""返回参考文献区起始行号；找不到返回 -1。

    判定要同时满足四条，缺一条都会误切：
      1. **整行只有标题**（尾部 `\s*$`）—— 否则正文里 "see the references [3]"
         会被当成标题。
      2. **行足够短**（<=60 字符）—— 真正的参考文献标题就那么几个词。
      3. **位置靠后**（>25%）—— 论文开头可能有 "References" 字样。
      4. **从后往前找，取最后一个** —— 参考文献区一定在文末。

    实测：247 篇里 225 篇能定位到，中位位置 94%，最早的一篇在 26%
    （短论文，AI 声明后面直接就是 References，已逐行核对过）。
    """
    lines = text.splitlines()
    lo = max(1, int(len(lines) * 0.25))
    for i in range(len(lines) - 1, lo - 1, -1):
        s = lines[i].strip()
        if s and len(s) <= 60 and REF_HEADING.match(s):
            return i
    return -1


def body_without_references(text: str) -> tuple[str, bool]:
    """返回 (正文, 是否成功裁掉参考文献区)。

    裁不掉时**原样返回并置 False**，不猜、不截 —— 静默砍掉一半正文
    比留着参考文献严重得多。调用方应把这个布尔值报出来，
    让读者知道这份语料里有多少篇是按哪种口径测的。
    """
    if not text:
        return text, False
    cut = find_reference_start(text)
    if cut < 0:
        return text, False
    lines = text.splitlines()[:cut]
    body = "\n".join(lines).rstrip()
    # 参考文献区占全文 90% 以上时，剩下这点不像正文，宁可判定为裁剪失败
    if len(body) < 0.1 * len(text):
        return text, False
    return body, True


def load_path(target: str | Path, max_chars: int = 2_000_000) -> list[Paper]:
    """载入单个 PDF。失败时返回 fulltext=None 的条目（不抛错，由调用方统计）。"""
    p = Path(target)
    text, backend = parse_pdf(p, max_chars)
    title = _guess_title(text, p.stem) if text else p.stem
    year = None
    m = re.search(r"\b(19|20)\d{2}\b", text[:1500]) if text else None
    if m:
        year = int(m.group(0))
    return [Paper(id=f"file:{p.stem}", title=title,
                  abstract=(text or "")[:4000], fulltext=text,
                  published=f"{year}-01-01" if year else "",
                  categories=["local"], abs_url=str(p), source="local")]


def load_paths(targets: list[str], max_chars: int = 2_000_000) -> list[Paper]:
    """载入若干文件或目录（目录递归找 *.pdf）。"""
    files: list[Path] = []
    for t in targets:
        p = Path(t)
        if p.is_dir():
            files += sorted(p.rglob("*.pdf"))
        elif p.exists():
            files.append(p)
        else:
            raise FileNotFoundError(f"路径不存在：{p}")
    if not files:
        raise FileNotFoundError(f"在 {targets} 下没找到任何 PDF")
    out: list[Paper] = []
    for f in files:
        out += load_path(f, max_chars)
    return out
