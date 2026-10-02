"""统一 CLI —— 这是「挂到任意 Harness」的边界

设计契约（Harness 集成要求 H1–H6）
------------------------------------
H1 无状态    ：所有持久化走文件系统；进程内不保存会话
H2 契约显式  ：每个子命令有 --help；stdout 为 JSON 或 Markdown；退出码有意义
              0 成功 / 1 校验失败 / 2 数据源失败 / 3 参数错误
H3 可脱离 LLM：`--llm none` 是默认；全流程纯 stdlib 可跑
H6 失败显式  ：数据源不可达、库缺失、schema 不符 → 抛错并给建议，不静默降级

任何 harness 的接法都只是：
    subprocess.run([...,'fieldmate','compare','--query','...','--format','json'])
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

EXIT_OK, EXIT_VALIDATION, EXIT_SOURCE, EXIT_ARGS, EXIT_NO_STRONG, EXIT_REFUTED = 0, 1, 2, 3, 4, 5

_ROOT = Path(__file__).resolve().parents[1]


def _resolve_query(args) -> str:
    """按优先级解析检索式：--query > --query-file > 环境变量 FIELDMATE_QUERY。

    为什么需要这么多入口：arXiv 检索式天然含空格与引号，而 shell、
    subprocess、各家 harness 的参数传递对它们的处理**都不一样**
    （PowerShell 会直接拆词）。所以必须提供不经过参数解析的旁路，
    否则这个工具在真实 harness 里会莫名其妙地搜不到东西——
    这类"看起来是数据问题、其实是引用问题"的失败最难查。
    """
    import os
    if getattr(args, "query", None):
        return args.query
    qf = getattr(args, "query_file", None)
    if qf:
        return Path(qf).read_text(encoding="utf-8").strip()
    return (os.environ.get("FIELDMATE_QUERY") or "").strip()


def _cmd_compare(args) -> int:
    from .sources.arxiv import RateLimiter, collect
    from .sources.local import load_paths
    from .match.patterns import load_library, load_rules, match_all
    from .compare.matrix import (build_matrix, defect_stats, matrix_csv,
                                matrix_json, matrix_markdown)

    library = load_library(args.library)
    rules = load_rules(args.rules)

    papers = []
    query = _resolve_query(args)
    query_desc = query
    source = "mixed"
    if query:
        try:
            papers = collect(query, target=args.limit,
                             limiter=RateLimiter(args.interval))
        except RuntimeError as e:
            print(f"[source] arXiv 检索失败：{e}\n"
                  f"        提示：arXiv 要求请求间隔 >=3s；也可改用 --path 走本地 PDF。",
                  file=sys.stderr)
            return EXIT_SOURCE
        source = "arxiv"
    if args.path:
        try:
            papers += load_paths(args.path)
        except Exception as e:                          # noqa: BLE001
            print(f"[source] 本地 PDF 读取失败：{e}", file=sys.stderr)
            return EXIT_SOURCE
        if not args.query:
            query_desc = f"local:{args.path}"
    # 离线批量语料。与 gaps 的 --corpus 同一套语义：只读本地缓存、不联网。
    # 早先只有 gaps 支持它，compare 不支持 —— 横向对比矩阵恰恰是最该在
    # 全量语料上看的东西，却只能喂两篇本地 PDF，这是个自相矛盾的接口。
    if getattr(args, "corpus", None):
        from .sources.fulltext import load_manifest
        try:
            bulk, cst = load_manifest(args.corpus, cache=args.cache, verbose=True)
        except FileNotFoundError as e:
            print(f"[source] {e}", file=sys.stderr)
            return EXIT_SOURCE
        papers += bulk
        source = "corpus" if source == "mixed" else f"{source}+corpus"
        if cst["n_fulltext"] == 0 and not args.query and not args.path:
            print("[warn] 该 manifest 没有任何可用全文 —— 结论只能算线索，不能当证据。",
                  file=sys.stderr)
            source = "local"

    if not papers:
        print("[validate] 没有取到任何论文，检查 --query / --path / --limit", file=sys.stderr)
        return EXIT_VALIDATION

    if args.dry_run:
        print(json.dumps({"query": query_desc, "source": source,
                          "retrieved": len(papers),
                          "papers": [p.to_dict() for p in papers]},
                         ensure_ascii=False, indent=2))
        return EXIT_OK

    fp_info = _maybe_fulltext(papers, args, query_desc)
    matches = match_all(papers, library, rules)
    rows = build_matrix(papers, matches)
    stats = defect_stats(rows, library)

    if args.format == "markdown":
        print(matrix_markdown(rows, stats, query_desc, source, library))
    elif args.format == "csv":
        print(matrix_csv(rows), end="")
    else:
        print(matrix_json(rows, stats, query_desc, source))

    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(matrix_json(rows, stats, query_desc, source), encoding="utf-8")
        print(f"[out] {p}", file=sys.stderr)
    return EXIT_OK


def _maybe_fulltext(papers, args, query_desc: str) -> dict | None:
    """按 --fulltext 抓取并解析全文，打印进度与语料指纹。

    刻意**不静默**：抓不到全文时框架会拒绝给出 STRONG 结论（退出码 4），
    用户必须知道「为什么这次结论不可用」，否则会误以为是数据有问题。
    """
    if not getattr(args, "fulltext", False):
        n_ft = sum(1 for p in papers if p.fulltext)
        if not n_ft:
            print("[hint] 本次未抓全文（--fulltext）。仅标题/摘要下，"
                  "「是否报告 Δt/分辨率/噪声模型」这类信号几乎必然为缺失——"
                  "摘要这种体裁基本不写它们。结论只会是 WEAK 级。", file=sys.stderr)
        return None
    from pathlib import Path as _P
    from .sources.fulltext import attach_fulltext, corpus_fingerprint, default_cache
    cache = _P(args.cache) if args.cache else default_cache()
    print(f"[fulltext] 开始抓取（缓存目录 {cache}）…", file=sys.stderr)
    st = attach_fulltext(papers, cache=cache, verbose=True)
    fp = corpus_fingerprint(papers)
    print(f"[fulltext] 下载={st['downloaded']} 命中缓存={st['cached']} "
          f"解析成功={st['parsed']} 失败={st['failed']} 无PDF={st['no_pdf']}", file=sys.stderr)
    print(f"[fulltext] 语料指纹 {fp}", file=sys.stderr)
    if st["parsed"] < 0.5 * max(len(papers), 1):
        print("[fulltext] ⚠ 半数以上论文未取到全文；"
              "本轮结论将受限于摘要层，框架会自动下调证据强度。", file=sys.stderr)
    return {"stat": st, "fingerprint": fp, "query": query_desc}


def _cmd_harvest(args) -> int:
    """自动语料构建：抓取 → 过相关性闸门 → 下载全文 → 写出带出处的清单。"""
    from .sources.corpus import harvest, harvest_markdown
    m = harvest(per_query=args.per_query, only=args.only,
                require_term=not args.no_term_gate,
                do_download=not args.no_download, interval=args.interval,
                out=args.out, verbose=True)
    if args.format == "json":
        print(json.dumps(m, ensure_ascii=False, indent=2))
    else:
        print()
        print(harvest_markdown(m))
    return EXIT_OK if m["counts"]["accepted"] else EXIT_NO_STRONG


def _cmd_list(args) -> int:
    """列出本包的实际内容：缺陷库、检测规则覆盖、语料来源。

    注意：**求解器不在本包里**。fieldmate 只做评测基础设施，
    被评测的求解器来自 pfdenoise（或任何外部实现）。
    读论文/横向对比/预注册核验才是本包的职责。
    """
    from .match.patterns import load_library, load_rules
    from .eval.prf import load_goldset
    lib = load_library()
    rules = load_rules()
    rule_ids = {r["id"] for r in rules.get("rules", [])}
    gold = load_goldset()
    n_t = sum(1 for g in gold if g.get("corpus") == "target")

    print(f"缺陷库 {len(lib)} 条　可执行检测规则 {len(rule_ids)} 条　"
          f"人工标注 gold set {len(gold)} 条（其中目标领域 {n_t} 条）\n")
    print("缺陷库与规则覆盖：")
    for d in lib:
        mark = "✔" if d["id"] in rule_ids else "✖"
        print(f"  [{mark}] {d['id']:<12} {d.get('class', '?'):<15} "
              f"sev={d.get('severity', '?'):<7} {d['title'][:50]}")
    miss = [d["id"] for d in lib if d["id"] not in rule_ids]
    if miss:
        print(f"\n以下缺陷【无】可执行检测规则（只能被人读，无法被复用）：{miss}")
    print("\n语料来源：")
    print("  - libraries/corpus.json　人工指定的目标论文（计算数学方向的主力来源）")
    print("  - arXiv 检索　　　　　仅适用 CS/ML 主题；计算数学方向实测无效，见 README")
    print("\n被评测的求解器不在本包内（见 pfdenoise）。")
    return EXIT_OK


def _load_results(path):
    """读结果文件。保留顶层元信息（res_floor / noise / created），
    只把 rows 展开 —— 早先只取 rows，导致顶层 res_floor 丢失、
    预注册校验误报「缺少 res_floor 对照」。"""
    import json as _json
    r = _json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(r, dict):
        return {"rows": r}
    out = dict(r)
    if "rows" not in out and isinstance(r.get("matrix"), list):
        out["rows"] = r["matrix"]
    return out


def _cmd_read(args) -> int:
    """阅读卡：五槽抽取 + 按阅读目的渐进披露。"""
    from .extract.disclose import card_markdown, reading_card_l1, reading_card_l2
    from .extract.slots import cross_assumptions, extract_slots
    from .sources.local import parse_pdf
    from .sources.arxiv import Paper
    from .eval.prf import load_corpus
    import json as _json

    corpus = load_corpus()
    src = []
    for pid, path in (corpus.get("papers") or {}).items():
        src.append((pid, path))
    for p in (args.path or []):
        src.append((Path(p).stem, p))

    cards, slots_list = [], []
    for pid, path in src:
        text, _ = parse_pdf(path)
        paper = Paper(id=pid, title=Path(path).stem, abstract="")
        if not text:
            print(f"[skip] {pid}：无法解析 {path}", file=sys.stderr)
            continue
        ps = extract_slots(paper, text, per_slot=args.per_slot)
        c1 = reading_card_l1(ps)
        if args.l2:
            c1.update(reading_card_l2(ps))
        cards.append(c1)
        slots_list.append(ps)
    if not cards:
        print("[validate] 没有可用的全文", file=sys.stderr)
        return EXIT_SOURCE

    if args.format == "json":
        payload = {"cards": cards}
        shared = cross_assumptions(slots_list)
        if shared:
            payload["shared_assumptions"] = shared
        print(_json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(card_markdown(cards, purpose=args.purpose, l2=args.l2))
        shared = cross_assumptions(slots_list)
        if shared:
            print("\n## 跨论文共同假设（「N 篇都假设 X，但没人验证」是最典型的立项切口）\n")
            for s in shared:
                print(f"- {s['shared_by']} 篇共享：{s['papers']}")
            print("\n> 上面只统计了词头重合的句子，是**粗筛**。请打开对应槽位人读确认。")
    return EXIT_OK


def _cmd_prereg(args) -> int:
    """预注册：新建模板 / 校验 / 看必填对照。"""
    from .exp.prereg import new_template, prereg_markdown, save, validate
    import json as _json
    if args.init:
        d = new_template(args.id or "exp-001")
        if args.out:
            save(d, args.out)
            print(f"[out] {args.out}", file=sys.stderr)
        print(_json.dumps(d, ensure_ascii=False, indent=2))
        return EXIT_OK
    if not args.prereg:
        print("[validate] 需要 --prereg <文件>，或用 --init 生成模板", file=sys.stderr)
        return EXIT_ARGS
    d = _json.loads(Path(args.prereg).read_text(encoding="utf-8"))
    problems = validate(d, _load_results(args.results) if args.results else None)
    if args.format == "json":
        print(_json.dumps({"ok": not problems, "problems": problems},
                         ensure_ascii=False, indent=2))
    else:
        print(prereg_markdown(d, problems))
    return EXIT_OK if not problems else EXIT_VALIDATION


def _cmd_verify(args) -> int:
    """结果核验：把实测结果对回预注册。"""
    from .exp.verify import verify_file, verify_markdown
    import json as _json
    if not args.prereg or not args.results:
        print("[validate] 需要 --prereg 与 --results", file=sys.stderr)
        return EXIT_ARGS
    v = verify_file(args.prereg, args.results)
    d = _json.loads(Path(args.prereg).read_text(encoding="utf-8"))
    if args.format == "json":
        print(_json.dumps(v.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(verify_markdown(v, d))
    if args.out:
        Path(args.out).write_text(_json.dumps(v.to_dict(), ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"[out] {args.out}", file=sys.stderr)
    # 5 = 有假设被真推翻。负结果本身不是命令失败，但调用方应当知道。
    return EXIT_REFUTED if v.refuted else EXIT_OK


def _cmd_evaluate(args) -> int:
    """规则体检：用人工标注的 gold set 算各检测项的精确率/召回率。"""
    from .eval.prf import _load_texts, evaluate_items, load_corpus, load_goldset, prf_json, prf_markdown
    from .sources.fulltext import default_cache

    gold = load_goldset(args.gold)
    corpus = load_corpus()
    cache = Path(args.cache) if args.cache else Path(
        corpus.get("cache_dir") or default_cache())
    texts = _load_texts(cache, corpus)
    need = {g["paper"] for g in gold}
    missing = sorted(p for p in need if not texts.get(p))
    if missing:
        print(f"[warn] gold set 引用的 {len(missing)} 篇没有全文：{missing}\n"
              f"       这些样本会按「无文本」参与评估（等价于判负），结论不可用。\n"
              f"       检查 libraries/corpus.json 里的路径，或先跑 gaps --fulltext。",
              file=sys.stderr)
        if args.strict and missing:
            return EXIT_SOURCE
    prf = evaluate_items(gold, texts)
    n_ok = sum(r.tp + r.fp + r.fn + r.tn for r in prf)
    notes = _failure_notes(gold, prf, texts)
    print(prf_json(prf, n_ok) if args.format == "json"
          else prf_markdown(prf, n_ok, notes))
    return EXIT_OK


def _failure_notes(gold, prf, texts) -> list[str]:
    """把被规则误判为「已报告」的案例列出来——这是修规则时最需要看的东西。"""
    from .compare.matrix import REPORT_ITEMS
    from .compare.matrix import re_search
    pats = {n: p for n, p, _ in REPORT_ITEMS}
    out = []
    for g in gold:
        if int(g["label"]) == 0:
            t = texts.get(g["paper"], "")
            if t and re_search(pats.get(g["item"], "$^"), t):
                out.append(f"`{g['item']}` 在 **{g['paper']}** 上假阳性："
                           f"{g.get('evidence', '')[:120]}")
    return out


def _cmd_gaps(args) -> int:
    """精进点生成：横向对比 -> 可执行精进点候选（含证据强度分级）。"""
    from .sources.arxiv import RateLimiter, collect
    from .sources.local import load_paths
    from .sources.fulltext import load_manifest
    from .match.patterns import load_library, load_rules, match_all
    from .compare.matrix import build_matrix, defect_stats
    from .gaps.mine import gaps_json, gaps_markdown, mine_gaps

    library = load_library(args.library)
    rules = load_rules(args.rules)
    query = _resolve_query(args)

    papers = []
    if query:
        try:
            papers = collect(query, target=args.limit, limiter=RateLimiter(args.interval))
        except RuntimeError as e:
            print(f"[source] arXiv 检索失败：{e}", file=sys.stderr)
            return EXIT_SOURCE
    if args.corpus:
        # 离线批量语料：manifest + PDF 缓存，不联网。
        # 注意它和 --query 是**并集**关系而不是替代关系 —— 检索结果通常只有摘要，
        # 缓存语料才有全文；两者合并后才是完整的证据面。
        try:
            bulk, cst = load_manifest(args.corpus, cache=args.cache, verbose=True)
        except FileNotFoundError as e:
            print(f"[source] {e}", file=sys.stderr)
            return EXIT_SOURCE
        papers += bulk
        if not cst["n_fulltext"]:
            print("[warn] 该 manifest 没有任何可用全文 —— 结论只能算线索，不能当证据。",
                  file=sys.stderr)
    if args.path:
        try:
            papers += load_paths(args.path)
        except Exception as e:                          # noqa: BLE001
            print(f"[source] 本地 PDF 读取失败：{e}", file=sys.stderr)
            return EXIT_SOURCE
    if not papers:
        print("[validate] 没有取到论文", file=sys.stderr)
        return EXIT_VALIDATION

    ft = _maybe_fulltext(papers, args, query)
    rows = build_matrix(papers, match_all(papers, library, rules))
    stats = defect_stats(rows, library)
    gaps = mine_gaps(rows, stats, library, min_rate=args.min_rate, min_n=args.min_n)

    md = gaps_markdown(gaps, query, len(rows)) if args.format == "markdown" \
        else gaps_json(gaps, query, len(rows))
    if ft:
        md = md.replace("## 精进点候选\n",
                        f"## 精进点候选\n\n> 语料指纹 `{ft['fingerprint']}`\n")
    print(md)
    if args.out:
        Path(args.out).write_text(gaps_json(gaps, query, len(rows)), encoding="utf-8")
        print(f"[out] {args.out}", file=sys.stderr)
    # 退出码沿用「无 STRONG 级精进点」这一事实：2=不可作为立项依据
    return EXIT_OK if any(g.strength == "STRONG" for g in gaps) else 4


def _cmd_topics(args) -> int:
    """主题空间审计：哪些方向有、哪些方向一篇都没有。

    与 `coverage` 的分工：coverage 查**给定术语**的覆盖，
    topics 查**主题空间**的分布与空白。两者都补 `gaps` 看不见的那一半 ——
    `gaps` 只看报告规范缺失（报不报 Δt、报不报分辨率）。

    ⚠ 词层面匹配，词义未核验：每条命中都附原文片段，分诊看片段不看计数。
    """
    from .sources.fulltext import load_manifest
    from .audit.topics import audit_topics, report_markdown
    if not args.corpus:
        print("请用 --corpus 指定检索 manifest（如 libraries/corpus_bulk.json）",
              file=sys.stderr)
        return EXIT_VALIDATION
    try:
        papers, cst = load_manifest(args.corpus, cache=args.cache, verbose=True)
    except FileNotFoundError as e:
        print(f"[source] {e}", file=sys.stderr)
        return EXIT_SOURCE
    au = audit_topics(papers)
    print(report_markdown(au, show=args.show))
    if args.out:
        Path(args.out).write_text(json.dumps(au.to_dict(), ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"[out] {args.out}", file=sys.stderr)
    return EXIT_OK


def _cmd_coverage(args) -> int:
    """语料覆盖审计：我的语料够不够支撑我要做的事。

    与 gaps 的分工：gaps 看**领域里**缺什么，coverage 看**我的语料**够不够。
    语料可以被系统性地抓偏（实测 arXiv 上 Langevin 相关命中的「11 篇」里
    真正能拿来对比的只有 2~3 篇），而 gaps 不会为此报警。
    """
    from .sources.fulltext import load_manifest
    from .audit.coverage import audit_coverage, report_markdown
    if not args.corpus:
        print("请用 --corpus 指定检索 manifest（如 libraries/corpus_bulk.json）",
              file=sys.stderr)
        return EXIT_VALIDATION
    try:
        papers, cst = load_manifest(args.corpus, cache=args.cache, verbose=True)
    except FileNotFoundError as e:
        print(f"[source] {e}", file=sys.stderr)
        return EXIT_SOURCE
    rep = audit_coverage(papers)
    print(report_markdown(rep) if args.format == "markdown"
          else json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        print(f"[out] {args.out}", file=sys.stderr)
    # 退出码沿用「有没有完全没覆盖的技术线」：有 = 3（提醒别拿它当证据源）
    return EXIT_OK if not rep.by_verdict("NO_COVERAGE") else 3


def _cmd_patterns(args) -> int:
    from .match.patterns import load_library, load_rules
    lib = load_library(args.library)
    rules = load_rules(args.rules)
    rule_ids = {r["id"] for r in rules.get("rules", [])}
    lib_ids = {d["id"] for d in lib}
    by_id = {d["id"]: d for d in lib}
    runtime_ids = {d["id"] for d in lib if d.get("detect_by") == "runtime"}
    if args.json:
        print(json.dumps({
            "library": lib, "rules": rules,
            "n_defects": len(lib), "n_rules": len(rule_ids),
            # 两类分开：前者是欠账，后者按设计就没有
            "missing_rules": sorted(lib_ids - rule_ids - runtime_ids),
            "runtime_only": sorted(lib_ids & runtime_ids),
            "orphan_rules": sorted(rule_ids - lib_ids),
        }, ensure_ascii=False, indent=2))
        return EXIT_OK
    print("缺陷库：{} 条；检测规则：{} 条\n".format(len(lib), len(rule_ids)))
    for d in lib:
        if d["id"] in rule_ids:
            has = "✔"
        elif d["id"] in runtime_ids:
            has = "— 运行时缺陷（按设计无文本规则）"
        else:
            has = "✖ 无可执行规则"
        print("[{}] {}  {}".format(d["id"], d["title"], has))
        print("      class={} severity={} status={}".format(
            d.get("class"), d.get("severity"), d.get("status")))
        print("      detection: {}".format(d["detection"][:110]))
        if d["id"] in runtime_ids:
            print("      运行时原因: {}".format(d.get("runtime_reason", "")[:110]))
    missing_rules = sorted(rule_ids - lib_ids)
    # 分两栏。早先把两者混在一张列表里报，读的人要么给跑代码才看得见的缺陷
    # 硬写一条永远不触发的规则（制造假信号），要么整张列表当真话忽略 ——
    # 两个方向都是自伤。
    missing_lib = sorted(lib_ids - rule_ids - runtime_ids)
    runtime_only = sorted(runtime_ids)
    if missing_rules:
        print("\n规则引用了库中不存在的缺陷：{}".format(missing_rules))
    if missing_lib:
        print("\n以下缺陷【无】可执行检测规则（该做而没做，R1 风险）：")
        for i in missing_lib:
            print("  - {}".format(i))
    else:
        print("\n✔ 所有文本可检测的缺陷都已有可执行规则。")
    if runtime_only:
        print("\n以下 {} 条是**运行时缺陷**，判据在代码执行里，论文文本查不到：".format(
            len(runtime_only)))
        for i in runtime_only:
            print("  - {}  {}".format(i, by_id.get(i, {}).get("runtime_reason", "")))
    return EXIT_OK


def _cmd_sources(args) -> int:
    from .sources.arxiv import collect
    from .sources.local import load_paths
    out = {}
    query = _resolve_query(args)
    if not query and not args.path:
        # 早先版本这里直接打印 `{}` 然后 `any(...)` 对空字典求值得到 False，
        # 于是退出码 2 且没有任何说明 —— 一个「体检数据源」子命令，
        # 不带参数运行时既没说该带什么，也没说自己失败了。
        # 静默的怪失败比报错更难查，违反本项目自己的纪律。
        print("请至少指定一个数据源：--query / --query-file（arXiv）或 --path（本地 PDF）",
              file=sys.stderr)
        return EXIT_VALIDATION
    if query:
        try:
            ps = collect(query, target=args.limit)
            out["arxiv"] = {"query": query, "retrieved": len(ps),
                            "sample": [{"id": p.id, "year": p.year, "title": p.title}
                                       for p in ps[:5]]}
        except RuntimeError as e:
            out["arxiv_error"] = str(e)
    if args.path:
        try:
            ps = load_paths(args.path)
            out["local"] = {"path": args.path, "parsed": len(ps),
                            "failed": sum(1 for p in ps if p.fulltext is None),
                            "sample": [{"id": p.id, "title": p.title[:70]} for p in ps[:5]]}
        except Exception as e:                          # noqa: BLE001
            out["local_error"] = str(e)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    # 判定要看**实际取到的源**里有没有出错，而不是对可能为空的字典做 any() ——
    # any({}) 是 False，会把「什么都没查」误报成「查失败了」。
    return EXIT_OK if out and not any("error" in k for k in out) else EXIT_SOURCE


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="fieldmate",
        description="fieldmate：跨论文横向对比 + 缺陷库匹配（harness 无关，纯 stdlib）")
    sub = p.add_subparsers(dest="cmd", required=True)

    hv = sub.add_parser("harvest", help="自动语料构建：检索 + 相关性闸门 + 下载全文")
    hv.add_argument("--per-query", type=int, default=10, dest="per_query")
    hv.add_argument("--only", default="high", choices=["high", "all"],
                    help="high=只用验证过的高纯度检索式（默认）；all=含被污染的检索式")
    hv.add_argument("--no-term-gate", action="store_true",
                    help="关掉词法闸门（只保留学科闸门）—— 用来测闸门的贡献")
    hv.add_argument("--no-download", action="store_true", help="只抓取不下全文")
    hv.add_argument("--interval", type=float, default=3.0, help="arXiv 请求间隔（秒）")
    hv.add_argument("--out", default=None)
    hv.add_argument("--format", choices=["markdown", "json"], default="markdown")
    hv.set_defaults(func=_cmd_harvest)

    lsub = sub.add_parser("list", help="列出可用的曲面/噪声/求解器")
    lsub.set_defaults(func=_cmd_list)

    c = sub.add_parser("compare", help="检索 + 匹配 + 出横向对比矩阵")
    c.add_argument("--query", help="arXiv 检索式，如 'all:\"mesh denoising\"'")
    c.add_argument("--query-file", dest="query_file",
                   help="从文件读检索式（推荐：绕开 shell 的引号/空格处理）")
    c.add_argument("--path", nargs="*", default=None, help="本地 PDF 或目录")
    c.add_argument("--corpus", default=None,
                   help="检索 manifest 语料（corpus_bulk.json 等）；"
                        "只读本地缓存不联网，用于离线批量横向对比")
    c.add_argument("--limit", type=int, default=30)
    c.add_argument("--interval", type=float, default=3.0, help="arXiv 请求间隔（秒，官方要求>=3）")
    c.add_argument("--format", choices=["markdown", "json", "csv"], default="markdown")
    c.add_argument("--out", help="同时把 JSON 结果写到该路径")
    c.add_argument("--library", help="缺陷库 jsonl 路径")
    c.add_argument("--rules", help="检测规则 json 路径")
    c.add_argument("--dry-run", action="store_true", help="只取回论文元数据，不做匹配")
    c.add_argument("--fulltext", action="store_true",
                   help="下载并解析 arXiv 全文（强烈建议：只读摘要时信号基本不可用）")
    c.add_argument("--cache", default=None,
                   help="PDF 缓存目录（默认 .fieldmate-cache/arxiv_pdfs）")
    c.set_defaults(func=_cmd_compare)

    g = sub.add_parser("gaps", help="从横向对比挖精进点（含证据强度分级）")
    g.add_argument("--query")
    g.add_argument("--query-file", dest="query_file")
    g.add_argument("--path", nargs="*", default=None)
    g.add_argument("--corpus", default=None,
                   help="检索 manifest 语料（corpus_bulk.json 等）；"
                        "只读本地缓存不联网，配合 --fulltext 之外的离线批量分析")
    g.add_argument("--limit", type=int, default=30)
    g.add_argument("--interval", type=float, default=3.0)
    g.add_argument("--min-rate", type=float, default=0.5, help="触发精进点所需的最低缺失率")
    g.add_argument("--min-n", type=int, default=5, help="某族至少多少篇才纳入分析")
    g.add_argument("--format", choices=["markdown", "json"], default="markdown")
    g.add_argument("--out")
    g.add_argument("--library")
    g.add_argument("--rules")
    g.add_argument("--fulltext", action="store_true", help="下载并解析 arXiv 全文")
    g.add_argument("--cache", default=None, help="PDF 缓存目录")
    g.set_defaults(func=_cmd_gaps)

    rd = sub.add_parser("read", help="五槽抽取 + 渐进式披露阅读卡")
    rd.add_argument("--path", nargs="*", default=None,
                    help="本地 PDF；不给则用 libraries/corpus.json 里的目标论文")
    rd.add_argument("--purpose", default="beat",
                    choices=["implement", "beat", "cite", "build-on"],
                    help="阅读目的，决定展开哪些槽位")
    rd.add_argument("--l2", action="store_true", help="展开 L2 细节（默认只给 L1 一屏）")
    rd.add_argument("--per-slot", type=int, default=4, dest="per_slot")
    rd.add_argument("--format", choices=["markdown", "json"], default="markdown")
    rd.set_defaults(func=_cmd_read)

    pr = sub.add_parser("prereg", help="实验预注册：新建模板或校验")
    pr.add_argument("--init", action="store_true", help="生成预注册模板")
    pr.add_argument("--id", help="预注册 id")
    pr.add_argument("--prereg", help="预注册 json 路径")
    pr.add_argument("--results", help="结果 json 路径（给了就一并查对照是否齐全）")
    pr.add_argument("--format", choices=["markdown", "json"], default="markdown")
    pr.add_argument("--out")
    pr.set_defaults(func=_cmd_prereg)

    vf = sub.add_parser("verify", help="结果核验：对照预注册给出三态判定")
    vf.add_argument("--prereg", required=True)
    vf.add_argument("--results", required=True)
    vf.add_argument("--format", choices=["markdown", "json"], default="markdown")
    vf.add_argument("--out")
    vf.set_defaults(func=_cmd_verify)

    e = sub.add_parser("evaluate", help="用人工标注 gold set 给检测规则做体检")
    e.add_argument("--gold", help="标注集 jsonl（默认 libraries/goldset.jsonl）")
    e.add_argument("--cache", default=None, help="PDF 缓存目录（默认 .fieldmate-cache/arxiv_pdfs）")
    e.add_argument("--format", choices=["markdown", "json"], default="markdown")
    e.add_argument("--strict", action="store_true", help="缺全文时直接失败")
    e.set_defaults(func=_cmd_evaluate)

    d = sub.add_parser("patterns", help="查看缺陷库与检测规则的覆盖情况")
    d.add_argument("--library")
    d.add_argument("--rules")
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=_cmd_patterns)

    v = sub.add_parser("coverage", help="语料覆盖审计：关键术语在语料里有没有支撑")
    v.add_argument("--corpus", required=False,
                   help="检索 manifest 语料（corpus_bulk.json 等）")
    v.add_argument("--cache", default=None, help="PDF 缓存目录")
    v.add_argument("--format", choices=["markdown", "json"], default="markdown")
    v.add_argument("--out", default=None, help="同时把 JSON 结果写到该路径")
    v.set_defaults(func=_cmd_coverage)

    w = sub.add_parser("topics", help="主题空间审计：哪些方向有、哪些方向一篇都没有")
    w.add_argument("--corpus", default=None,
                   help="检索 manifest 语料（corpus_bulk.json 等）")
    w.add_argument("--cache", default=None, help="PDF 缓存目录")
    w.add_argument("--show", type=int, default=4, help="每个主题列出前几篇的原文片段")
    w.add_argument("--out", default=None, help="把 JSON 结果写到该路径")
    w.set_defaults(func=_cmd_topics)

    s = sub.add_parser("sources", help="体检数据源（arXiv / 本地 PDF）")
    s.add_argument("--query")
    s.add_argument("--query-file", dest="query_file")
    s.add_argument("--path", nargs="*", default=None)
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(func=_cmd_sources)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
