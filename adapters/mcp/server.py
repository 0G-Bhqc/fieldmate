"""MCP server —— 挂进任何支持 MCP 的 harness

设计原则（docs/DESIGN.md §4）：
* 核心判定全在 fieldmate 里，这个 server 只做参数转发与结果序列化。
* **不引入任何 LLM SDK**：框架不知道宿主有没有模型，也不关心。
* 检索式允许含空格/引号——MCP 的 JSON 参数没有 shell 拆词问题，
  这正是 MCP 相对裸 CLI 的一个实际优势。

启动：
    pip install -e ".[mcp]"
    python adapters/mcp/server.py          # stdio transport（10 个工具，三项能力各有入口）

在宿主里注册（示例，MCP client 配置）：
    {"mcpServers": {"fieldmate": {"command": "python",
                                         "args": ["adapters/mcp/server.py"]}}}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 允许「从仓库根直接跑」与「pip install 后 import」两种方式
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fieldmate.compare.matrix import (  # noqa: E402
    build_matrix,
    defect_stats,
    matrix_csv,
    matrix_json,
    matrix_markdown,
)
from fieldmate.match.patterns import load_library, load_rules, match_all  # noqa: E402
from fieldmate.sources.arxiv import collect  # noqa: E402
from fieldmate.sources.local import load_paths  # noqa: E402


def _gather(query: str | None, paths: list[str] | None, limit: int) -> list:
    papers: list = []
    if query:
        papers += collect(query, target=limit)
    if paths:
        papers += load_paths(paths)
    return papers


def compare_papers(query: str | None = None, paths: list[str] | None = None,
                   limit: int = 20, fmt: str = "markdown",
                   library_path: str | None = None,
                   rules_path: str | None = None) -> str:
    """横向对比一批论文。

    Args:
        query: arXiv 检索式，可含空格与引号（无需 shell 转义）。
        paths: 本地 PDF 或目录列表，可与 query 混用。
        limit: 最多取多少篇。
        fmt: markdown | json | csv
    """
    lib = load_library(library_path)
    rules = load_rules(rules_path)
    papers = _gather(query, paths, limit)
    if not papers:
        return json.dumps({"ok": False,
                           "error": "没有取到论文；检查 query / paths / limit"}, ensure_ascii=False)
    matches = match_all(papers, lib, rules)
    rows = build_matrix(papers, matches)
    stats = defect_stats(rows, lib)
    if fmt == "json":
        return matrix_json(rows, stats, query or "", "mixed")
    if fmt == "csv":
        return matrix_csv(rows)
    return matrix_markdown(rows, stats, query or "", "mixed", lib)


def list_patterns(library_path: str | None = None,
                  rules_path: str | None = None) -> str:
    """列出缺陷库，并标注每条是否已有可执行检测规则。"""
    lib = load_library(library_path)
    rules = load_rules(rules_path)
    rule_ids = {r["id"] for r in rules.get("rules", [])}
    lib_ids = {d["id"] for d in lib}
    return json.dumps({
        "n_defects": len(lib), "n_rules": len(rule_ids),
        "defects": [{"id": d["id"], "title": d["title"], "class": d.get("class"),
                     "severity": d.get("severity"), "status": d.get("status"),
                     "has_executable_rule": d["id"] in rule_ids,
                     "detection": d.get("detection")} for d in lib],
        "missing_rules": sorted(lib_ids - rule_ids),
        "orphan_rules": sorted(rule_ids - lib_ids),
    }, ensure_ascii=False, indent=2)


def check_sources(query: str | None = None, paths: list[str] | None = None,
                  limit: int = 3) -> str:
    """体检数据源是否可用（arXiv 是否通、PDF 能否解析）。"""
    if not query and not paths:
        # 早先无参时返回 `{}` —— 一个什么都不做、也不说为什么的空结果。
        # MCP 客户端拿到这个只能猜：是没数据源，还是数据源坏了？
        return json.dumps({
            "ok": False,
            "error": "请至少指定一个数据源：query（arXiv 检索式）或 paths（本地 PDF 列表）",
        }, ensure_ascii=False)
    out: dict = {}
    if query:
        try:
            ps = collect(query, target=limit)
            out["arxiv"] = {"ok": True, "retrieved": len(ps),
                            "titles": [p.title for p in ps]}
        except Exception as e:                              # noqa: BLE001
            out["arxiv"] = {"ok": False, "error": str(e)}
    if paths:
        try:
            ps = load_paths(paths)
            out["local"] = {"ok": True, "parsed": len(ps),
                            "without_fulltext": sum(1 for p in ps if not p.fulltext),
                            "titles": [p.title[:80] for p in ps]}
        except Exception as e:                              # noqa: BLE001
            out["local"] = {"ok": False, "error": str(e)}
    return json.dumps(out, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- 三项能力入口
#
# 为什么原来只有 3 个工具
# ----------------------
# 早先只接了 compare / patterns / sources —— 也就是「读文献」这一半。
# 而这个项目承诺的三项能力里，**能力①找精进点（gaps）和能力③实验核验（verify）
# 根本没有 MCP 入口**。宿主 harness 挂上它，却用不上三分之二的东西。
# 「可挂载到任意 harness」这句话在能力①③上是**不成立的**，必须补。

def mine_gaps(corpus: str, paths: list[str] | None = None,
                   query: str | None = None, limit: int = 20,
                   min_rate: float = 0.5, min_n: int = 5,
                   fmt: str = "markdown") -> str:
    """能力①：从横向对比挖精进点（每条带出处名单与关联缺陷）。

    Args:
        corpus: 检索 manifest 语料路径（libraries/corpus_bulk.json 等），只读本地缓存。
        paths: 本地 PDF 或目录，可与 corpus 并集。
        query: arXiv 检索式（会联网，默认不填）。
        min_rate: 触发精进点所需的最低缺失率。
        min_n: 某族至少多少篇才纳入分析。
        fmt: markdown | json
    """
    from fieldmate.compare.matrix import build_matrix, defect_stats
    from fieldmate.gaps.mine import gaps_json, gaps_markdown, mine_gaps
    from fieldmate.sources.fulltext import load_manifest

    lib = load_library()
    rules = load_rules()
    papers: list = []
    if corpus:
        try:
            bulk, _cst = load_manifest(corpus)
        except FileNotFoundError as e:
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
        papers += bulk
    if paths:
        try:
            papers += load_paths(paths)
        except Exception as e:                              # noqa: BLE001
            return json.dumps({"ok": False, "error": f"本地 PDF 读取失败：{e}"},
                              ensure_ascii=False)
    if query:
        try:
            papers += collect(query, target=limit)
        except RuntimeError as e:
            return json.dumps({"ok": False, "error": f"arXiv 检索失败：{e}"},
                              ensure_ascii=False)
    if not papers:
        return json.dumps({"ok": False,
                           "error": "没有取到论文；检查 corpus / paths / query"},
                          ensure_ascii=False)
    matches = match_all(papers, lib, rules)
    rows = build_matrix(papers, matches)
    gaps = mine_gaps(rows, defect_stats(rows, lib), lib,
                     min_rate=min_rate, min_n=min_n)
    if fmt == "json":
        return gaps_json(gaps, query or "", len(rows))
    return gaps_markdown(gaps, query or "", len(rows))


def audit_coverage(corpus: str, fmt: str = "markdown") -> str:
    """语料覆盖审计：关键术语在语料里有没有支撑（按命中出处分层）。

    Args:
        corpus: 检索 manifest 语料路径。
        fmt: markdown | json
    """
    import json as _json

    from fieldmate.audit.coverage import audit_coverage, report_markdown
    from fieldmate.sources.fulltext import load_manifest
    try:
        papers, _ = load_manifest(corpus)
    except FileNotFoundError as e:
        return _json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    rep = audit_coverage(papers)
    if fmt == "json":
        return _json.dumps(rep.to_dict(), ensure_ascii=False, indent=2)
    return report_markdown(rep)


def audit_topics(corpus: str, show: int = 3) -> str:
    """主题空间审计：哪些方向有、哪些方向几乎没有（每条命中附原文片段）。

    Args:
        corpus: 检索 manifest 语料路径。
        show: 每个主题列出前几篇的原文片段。
    """
    import json as _json

    from fieldmate.audit.topics import audit_topics, report_markdown
    from fieldmate.sources.fulltext import load_manifest
    try:
        papers, _ = load_manifest(corpus)
    except FileNotFoundError as e:
        return _json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
    return report_markdown(audit_topics(papers), show=show)


def verify_result(prereg: str, results: str) -> str:
    """能力③：对照预注册核验结果，给出 SUPPORTED / REFUTED / INCONCLUSIVE 三态判定。

    Args:
        prereg: 预注册 json 路径（先跑 `fieldmate prereg --init` 生成模板）。
        results: 结果 json 路径。

    差异落在分辨率噪声内时判 INCONCLUSIVE —— 此时**无法区分**「方法更差」
    与「设置不足」，工具拒绝替你下结论。
    """
    import json as _json

    from fieldmate.exp.prereg import validate
    from fieldmate.exp.verify import verify
    p, r = Path(prereg), Path(results)
    for f, label in ((p, "prereg"), (r, "results")):
        if not f.exists():
            return _json.dumps({"ok": False, "error": f"{label} 文件不存在：{f}"},
                               ensure_ascii=False)
    try:
        pd = _json.loads(p.read_text(encoding="utf-8"))
        rd = _json.loads(r.read_text(encoding="utf-8"))
    except Exception as e:                                  # noqa: BLE001
        return _json.dumps({"ok": False, "error": f"JSON 解析失败：{e}"},
                           ensure_ascii=False)
    problems = validate(pd)
    v = verify(pd, rd.get("rows", []), {"res_floor": rd.get("res_floor")})
    out = v.to_dict()
    out["ok"] = True
    out["prereg_problems"] = problems
    return _json.dumps(out, ensure_ascii=False, indent=2)


def prereg_init(exp_id: str = "exp-001", out: str | None = None) -> str:
    """能力③前置：生成实验预注册模板（json）。没有预注册就不能跑 verify。

    Args:
        exp_id: 预注册 id，如 exp-001。
        out: 写出路径；不填则只返回模板内容。
    """
    import json as _json

    from fieldmate.exp.prereg import new_template, save
    d = new_template(exp_id)
    if out:
        save(d, out)
        d["written_to"] = out
    return _json.dumps(d, ensure_ascii=False, indent=2)


def evaluate_rules(gold: str | None = None, cache: str | None = None,
                   fmt: str = "markdown") -> str:
    """规则体检：用人工标注 gold set 算各检测项精确率/召回率。

    Args:
        gold: 标注集 jsonl 路径（默认包内 libraries/goldset.jsonl）。
        cache: PDF 缓存目录（默认 cwd 下 .fieldmate-cache/arxiv_pdfs）。
        fmt: markdown | json
    """
    import json as _json
    from pathlib import Path as _Path

    from fieldmate.compare.matrix import REPORT_ITEMS, re_search
    from fieldmate.eval.prf import (
        _load_texts,
        evaluate_items,
        load_corpus,
        load_goldset,
        prf_json,
        prf_markdown,
    )
    from fieldmate.sources.fulltext import default_cache

    goldset = load_goldset(gold)
    corpus = load_corpus()
    c = _Path(cache) if cache else _Path(corpus.get("cache_dir") or default_cache())
    texts = _load_texts(c, corpus)
    need = {g["paper"] for g in goldset}
    missing = sorted(p for p in need if not texts.get(p))
    prf = evaluate_items(goldset, texts)
    n_ok = sum(r.tp + r.fp + r.fn + r.tn for r in prf)
    notes = []
    pats = {n: p for n, p, _ in REPORT_ITEMS}
    for g in goldset:
        if int(g["label"]) == 0:
            t = texts.get(g["paper"], "")
            if t and re_search(pats.get(g["item"], "$^"), t):
                notes.append(f"`{g['item']}` 在 {g['paper']} 上假阳性："
                             f"{g.get('evidence', '')[:120]}")
    head = (_json.dumps({"missing_fulltext": missing}, ensure_ascii=False)
            + "\n") if missing else ""
    body = prf_json(prf, n_ok) if fmt == "json" else prf_markdown(prf, n_ok, notes)
    return head + body


def read_card(paths: list[str], purpose: str = "beat", l2: bool = False,
              per_slot: int = 4, fmt: str = "markdown") -> str:
    """能力②：五槽阅读卡——按阅读目的渐进披露一篇论文的候选句。

    Args:
        paths: 本地 PDF 路径列表（1~5 篇为宜）。
        purpose: implement 复现 / beat 超越 / cite 引用 / build-on 承接。
        l2: 展开原句（默认只给一屏摘要）。
        per_slot: 每槽最多候选句数。
        fmt: markdown | json
    """
    import json as _json
    from pathlib import Path as _Path

    from fieldmate.extract.disclose import card_markdown, reading_card_l1, reading_card_l2
    from fieldmate.extract.slots import extract_slots
    from fieldmate.sources.arxiv import Paper
    from fieldmate.sources.local import parse_pdf

    cards = []
    for p in paths:
        text, _backend = parse_pdf(_Path(p))
        if not text:
            continue
        paper = Paper(id=_Path(p).stem, title=_Path(p).stem, abstract="")
        ps = extract_slots(paper, text, per_slot=per_slot)
        c1 = reading_card_l1(ps)
        if l2:
            c1.update(reading_card_l2(ps))
        cards.append(c1)
    if not cards:
        return _json.dumps({"ok": False,
                            "error": '没有可解析的 PDF；检查路径或 pip install -e ".[pdf]"'},
                           ensure_ascii=False)
    if fmt == "json":
        return _json.dumps({"ok": True, "cards": cards}, ensure_ascii=False, indent=2)
    return card_markdown(cards, purpose=purpose, l2=l2)


# ---------------------------------------------------------------- MCP 接线
def _serve() -> int:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError:
        print("需要 MCP SDK：pip install -e \".[mcp]\"", file=sys.stderr)
        return 2
    srv = FastMCP("fieldmate")

    # 三项能力都要有入口，缺一项「可挂载」就只是句空话
    srv.tool()(compare_papers)          # 能力② 横向对比
    srv.tool()(read_card)               # 能力② 五槽阅读卡
    srv.tool()(mine_gaps)               # 能力① 精进点
    srv.tool()(verify_result)           # 能力③ 实验核验
    srv.tool()(prereg_init)             # 能力③ 预注册模板
    srv.tool()(audit_coverage)          # 语料够不够（terms）
    srv.tool()(audit_topics)            # 语料够不够（topic space）
    srv.tool()(evaluate_rules)          # 规则体检
    srv.tool()(list_patterns)
    srv.tool()(check_sources)
    srv.run()
    return 0


#: 挂进宿主时应该能看到的工具名。测试据此断言「三项能力都有入口」。
TOOL_NAMES = ("compare_papers", "read_card", "mine_gaps", "verify_result",
              "prereg_init", "audit_coverage", "audit_topics",
              "evaluate_rules", "list_patterns", "check_sources")


if __name__ == "__main__":
    sys.exit(_serve())
