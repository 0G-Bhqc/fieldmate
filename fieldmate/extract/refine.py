"""Assumption 槽精筛 —— 判断层的第一个落地任务（Phase 3）。

分工（DESIGN §1）
----------------
* **预滤器（脚本，确定性）**：用宽于槽位正则的词表把「可能含假设的句子」捞出来。
  这是召回层，宁可多捞——槽位正则在此前两篇真实论文上 Assumption 命中为 0。
* **精筛（LLM，候选）**：判断捞出的句子里哪些是**值得记录的未验证前提**，
  按 prompts/assumption_refine.md 的口径输出候选。
* **幻觉闸门（脚本，确定性）**：LLM 返回的 quote 必须**逐字命中**输入句子
  （空白归一后子串匹配），否则丢弃并记账——LLM 的输出永远不能引入
  正文里不存在的话。
* **人复核（最终）**：所有候选都标「未经人工确认」。

`--llm none`（不给 --llm-cmd）时本模块完全不参与，`read` 行为与之前一致。
"""
from __future__ import annotations

import re
from typing import Any

from .._paths import prompt_path
from ..llm import LLMError, run_with_cmd

# 宽召回词表：比槽位正则松得多——槽位正则要精确率（0 命中等于白干），
# 预滤器只要召回，判断交给 LLM，幻觉交给闸门。
_MARKER = re.compile(
    r"\b(assume[sd]?|assuming|assumption[s]?|presuppos\w+|"
    r"treat(ed)?\s+as|idealiz\w+|neglect\w+|postulat\w*|"
    r"hypothes[ei]\w*)\b"
    r"|假设|假定|理想化|忽略"
)
_KINDS = {"model", "scope", "numerical", "prior-knowledge", "other"}
_CONF = {"high", "medium", "low"}
_MIN_QUOTE = 15          # 归一化后最小长度，太短的 quote 无法可靠对回原文
_MAX_CANDIDATES = 12


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def assumption_prefilter(text: str, min_len: int = 30, max_len: int = 400,
                         max_sentences: int = 24) -> list[str]:
    """宽召回：把命中假设词表的句子按出现顺序捞出来（去重、限量）。"""
    from .slots import _sentences
    out: list[str] = []
    seen: set[str] = set()
    for sent in _sentences(text, min_len=min_len, max_len=max_len):
        if not _MARKER.search(sent):
            continue
        key = _norm(sent)
        if key in seen:
            continue
        seen.add(key)
        out.append(sent)
        if len(out) >= max_sentences:
            break
    return out


def validate_candidates(result: dict, source_sentences: list[str],
                        origin: str = "LLM") -> tuple[list[dict], list[str]]:
    """判定归脚本：对 LLM 候选做 schema 校验 + 幻觉闸门。

    返回 (合法候选, 被丢弃的说明列表)。丢弃的每一条都有理由，
    调用方应原样呈现给用户——静默丢弃等于又一次 D-REP-001。
    """
    dropped: list[str] = []
    if not isinstance(result, dict):
        return [], [f"{origin} result 不是对象"]
    cands = result.get("candidates", [])
    if not isinstance(cands, list):
        return [], [f"{origin} candidates 不是列表"]

    corpus = _norm(" ".join(source_sentences))
    kept: list[dict] = []
    seen_quotes: set[str] = set()
    for i, c in enumerate(cands):
        label = f"{origin}候选#{i + 1}"
        if not isinstance(c, dict):
            dropped.append(f"{label}：不是对象")
            continue
        quote = c.get("quote", "")
        kind = c.get("kind", "")
        conf = c.get("confidence", "")
        rationale = c.get("rationale", "")
        if not isinstance(quote, str) or _norm(quote) == "":
            dropped.append(f"{label}：quote 为空")
            continue
        nq = _norm(quote)
        if len(nq) < _MIN_QUOTE:
            dropped.append(f"{label}：quote 过短（<{_MIN_QUOTE} 字符），无法可靠对回原文")
            continue
        if nq not in corpus:
            dropped.append(f"{label}：幻觉——quote 不在输入句中（「{nq[:50]}…」）")
            continue
        if kind not in _KINDS:
            dropped.append(f"{label}：kind 非法（{kind!r}）")
            continue
        if conf not in _CONF:
            dropped.append(f"{label}：confidence 非法（{conf!r}）")
            continue
        if not isinstance(rationale, str) or not rationale.strip():
            dropped.append(f"{label}：rationale 为空")
            continue
        if nq in seen_quotes:
            continue
        seen_quotes.add(nq)
        kept.append({"quote": re.sub(r"\s+", " ", quote).strip(),
                     "kind": kind, "confidence": conf,
                     "rationale": rationale.strip()[:400],
                     "confirmed": False})
        if len(kept) >= _MAX_CANDIDATES:
            break
    return kept, dropped


def _chunks(text: str, chunk_chars: int = 6000, max_chunks: int = 3) -> list[str]:
    """无标记文本的分块扫描：等距取正文块（避开参考文献尾巴由调用方裁剪）。

    实测依据：两篇真实期刊论文全文几乎零假设类措辞（P1 只 1 处 simplif）——
    它们的假设是**隐式**的（参数取值、有效性范围、离散化选择被当作成立）。
    词表预滤对这类文本必然 0 召回，只能让 LLM 直接看正文块找隐式前提。
    """
    body = re.sub(r"[ \t]*\n[ \t]*", " ", text)
    body = re.sub(r"\s+", " ", body).strip()
    if len(body) <= chunk_chars:
        return [body] if body else []
    step = (len(body) - chunk_chars) // (max_chunks - 1)
    return [body[i * step: i * step + chunk_chars] for i in range(max_chunks)]


def refine_assumptions(text: str, llm_cmd: str, timeout: float = 180) -> dict[str, Any]:
    """对一段论文正文跑完整精筛管线，返回结构化结果（含丢弃记账）。

    双路设计：有假设类措辞 → 句级判断（assumption_refine）；
    无措辞（期刊论文的隐式假设）→ 分块扫描（assumption_scan）。
    两条路的幻觉闸门相同：quote 必须逐字对回喂给 LLM 的文本。
    """
    sents = assumption_prefilter(text)
    if sents:
        prompt = prompt_path("assumption_refine.md").read_text(encoding="utf-8")
        result = run_with_cmd(llm_cmd, {
            "task": "assumption_refine",
            "prompt": prompt,
            "data": {"sentences": sents},
        }, timeout=timeout)
        kept, dropped = validate_candidates(result, sents)
        return {"candidates": kept, "dropped": dropped, "n_prefiltered": len(sents),
                "mode": "sentence-refine"}

    # 无标记回退：分块扫描找隐式假设（真实期刊论文的主路径）
    chunks = _chunks(text)
    if not chunks:
        return {"candidates": [], "dropped": [], "n_prefiltered": 0, "mode": "chunk-scan",
                "skipped": "正文为空"}
    prompt = prompt_path("assumption_scan.md").read_text(encoding="utf-8")
    result = run_with_cmd(llm_cmd, {
        "task": "assumption_scan",
        "prompt": prompt,
        "data": {"chunks": chunks},
    }, timeout=timeout)
    kept, dropped = validate_candidates(result, chunks)
    return {"candidates": kept, "dropped": dropped, "n_prefiltered": 0,
            "mode": "chunk-scan", "n_chunks": len(chunks)}


def subdomain_check(llm_cmd: str, query: str, title: str, abstract: str,
                    timeout: float = 120) -> tuple[bool, str]:
    """闸门 C：LLM 判断论文是否真的在做检索式所指的子领域。

    返回 (relevant, reason)；LLM 失败按 H6 抛 LLMError，由调用方决定记账方式。
    """
    prompt = prompt_path("subdomain_filter.md").read_text(encoding="utf-8")
    result = run_with_cmd(llm_cmd, {
        "task": "subdomain_filter",
        "prompt": prompt,
        "data": {"query": query, "title": title, "abstract": abstract},
    }, timeout=timeout)
    relevant = result.get("relevant")
    reason = result.get("reason", "")
    if not isinstance(relevant, bool):
        raise LLMError(f"subdomain_filter 返回的 relevant 不是布尔值：{relevant!r}")
    if not isinstance(reason, str):
        reason = ""
    return relevant, reason.strip()[:200]
