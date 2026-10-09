"""统一 CLI —— 这是「挂到任意 Harness」的边界

设计契约（Harness 集成要求 H1–H6）
------------------------------------
H1 无状态    ：所有持久化走文件系统；进程内不保存会话
H2 契约显式  ：每个子命令有 --help；stdout 为 JSON 或 Markdown；退出码有意义
              0 成功 / 1 校验失败 / 2 数据源失败 / 3 参数错误（coverage 下 = 语料未覆盖）
              4 gaps 无 STRONG 级精进点（harvest 全被拒收同码）
              5 verify 有假设被真推翻（逐命令表见 docs/DESIGN.md）
H3 可脱离 LLM：`--llm none` 是默认；全流程纯 stdlib 可跑
H6 失败显式  ：数据源不可达、库缺失、schema 不符 → 抛错并给建议，不静默降级

任何 harness 的接法都只是：
    subprocess.run([...,'fieldmate','compare','--query','...','--format','json'])
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

EXIT_OK, EXIT_VALIDATION, EXIT_SOURCE, EXIT_ARGS, EXIT_NO_STRONG, EXIT_REFUTED = 0, 1, 2, 3, 4, 5
# coverage 用 3 表示「语料存在 NO_COVERAGE 技术线」—— 与 EXIT_ARGS 同值，
# 语义按**子命令**解释（coverage 没有位置参数冲突场景），逐命令对照表见 docs/DESIGN.md。
EXIT_NO_COVERAGE = 3

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
    from .compare.matrix import build_matrix, defect_stats, matrix_csv, matrix_json, matrix_markdown
    from .match.patterns import load_library, load_rules, match_all
    from .sources.arxiv import RateLimiter, collect
    from .sources.local import load_paths

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

    _maybe_fulltext(papers, args, query_desc)   # compare 不用语料指纹，只取抓取进度与告警
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
    from .sources.fulltext import attach_fulltext, corpus_fingerprint, default_cache
    cache = Path(args.cache) if args.cache else default_cache()
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
                out=args.out, verbose=True, llm_cmd=args.llm_cmd)
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
    from .eval.prf import load_goldset
    from .match.patterns import load_library, load_rules
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


def _collect_read_sources(corpus: dict, extra_paths: list[str]) -> list[tuple[str, str]]:
    """read 的输入源合并：目标语料（corpus.json）在前，--path 补充在后。

    按**解析后的真实路径**去重 —— 语料里已有的论文再显式传一次 --path，
    以前会出两张一样的阅读卡（真实测试 2026-10-03 发现）；同一篇以语料
    里的 id（P1-recon 这类语义名）为准。
    """
    from .eval.prf import _resolve_paper_path
    src: list[tuple[str, str]] = []
    seen: set[str] = set()
    for pid, path in (corpus.get("papers") or {}).items():
        resolved = _resolve_paper_path(path)
        key = str(resolved.resolve())
        if key in seen:
            continue
        seen.add(key)
        src.append((pid, str(resolved)))
    for p in extra_paths:
        resolved = _resolve_paper_path(p)
        key = str(resolved.resolve())
        if key in seen:
            continue
        seen.add(key)
        src.append((Path(p).stem, str(resolved)))
    return src


def _cmd_read(args) -> int:
    """阅读卡：五槽抽取 + 按阅读目的渐进披露。"""
    import json as _json

    from .eval.prf import load_corpus
    from .extract.disclose import card_markdown, reading_card_l1, reading_card_l2
    from .extract.slots import cross_assumptions, extract_slots
    from .sources.arxiv import Paper
    from .sources.local import parse_pdf

    corpus = load_corpus()
    src = _collect_read_sources(corpus, args.path or [])

    if args.refine_assumptions and not args.llm_cmd:
        print("[validate] --refine-assumptions 需要 --llm-cmd（宿主 LLM 命令，"
              "协议见 docs/DESIGN.md）；不给 --llm-cmd 时 read 行为保持不变", file=sys.stderr)
        return EXIT_ARGS

    from .extract.refine import refine_assumptions
    from .llm import LLMError

    refined: dict[str, dict] = {}
    cards, slots_list = [], []
    for pid, path in src:
        text, _ = parse_pdf(path)
        paper = Paper(id=pid, title=Path(path).stem, abstract="")
        if not text:
            print(f"[skip] {pid}：无法解析 {path}", file=sys.stderr)
            continue
        ps = extract_slots(paper, text, per_slot=args.per_slot)
        if args.refine_assumptions:
            try:
                refined[pid] = refine_assumptions(text, args.llm_cmd)
            except LLMError as e:
                print(f"[refine] {pid}：{e}", file=sys.stderr)
                refined[pid] = {"error": str(e), "candidates": [], "dropped": []}
        c1 = reading_card_l1(ps)
        if args.l2:
            c1.update(reading_card_l2(ps))
        cards.append(c1)
        slots_list.append(ps)
    if not cards:
        print("[validate] 没有可用的全文", file=sys.stderr)
        return EXIT_SOURCE

    if args.refine_assumptions and refined and args.format == "markdown":
        print("\n## Assumption 候选（LLM 精筛 · 未经人工确认）\n")
        for pid, rr in refined.items():
            print(f"### {pid}")
            if rr.get("error"):
                print(f"- ⛔ 精筛失败：{rr['error']}")
            elif not rr.get("candidates"):
                scope = (f"预滤 {rr['n_prefiltered']} 句" if rr.get("mode") != "chunk-scan"
                         else f"扫描 {rr.get('n_chunks', '?')} 个正文块")
                print(f"- （{scope}，LLM 未给出合格候选）")
            for c in rr.get("candidates", []):
                print(f"- [{c['kind']}/{c['confidence']}] {c['quote']}")
                print(f"    - 理由：{c['rationale']}")
            for d in rr.get("dropped", []):
                print(f"  - ⚠ 丢弃：{d}")
        print("\n> 判定归脚本：以上 quote 已逐字对回原文（幻觉剔除），kind/置信度仍需人读确认。")

    if args.format == "json":
        payload = {"cards": cards}
        shared = cross_assumptions(slots_list)
        if shared:
            payload["shared_assumptions"] = shared
        if refined:
            payload["refined_assumptions"] = refined
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
    import json as _json

    from .exp.prereg import new_template, prereg_markdown, save, validate
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
    import json as _json

    from .exp.verify import verify_file, verify_markdown
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


def _cmd_breakthrough(args) -> int:
    """突破点挖掘：从论文全文或摘要中逆向提取学术/数值瓶颈与改进方向。"""
    import json as _json

    from .research.breakthrough import breakthrough_markdown, discover_breakthroughs
    from .sources.local import parse_pdf_cached

    paper_path = args.path
    if not paper_path:
        print("[validate] 请指定待分析的论文路径：--path <paper.pdf>", file=sys.stderr)
        return EXIT_ARGS

    txt, _ = parse_pdf_cached(paper_path)
    if not txt:
        print(f"[source] 无法读取或解析论文全文：{paper_path}", file=sys.stderr)
        return EXIT_SOURCE

    bts = discover_breakthroughs(txt, paper_id=Path(paper_path).stem)
    if not bts:
        print("[breakthrough] 未在文中检测到明显物理/数值瓶颈模式", file=sys.stderr)
        return EXIT_NO_STRONG

    if args.format == "json":
        print(_json.dumps([b.to_dict() for b in bts], ensure_ascii=False, indent=2))
    else:
        print(breakthrough_markdown(bts, paper_desc=Path(paper_path).name))

    if args.out:
        Path(args.out).write_text(
            _json.dumps([b.to_dict() for b in bts], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[out] {args.out}", file=sys.stderr)

    return EXIT_OK


def _cmd_design(args) -> int:
    """实验设计：根据用户想法（--idea）或根据论文突破点自动生成可证伪预注册。"""
    import json as _json

    from .exp.prereg import prereg_markdown, save, validate
    from .research.breakthrough import BREAKTHROUGH_ARCHETYPES, Breakthrough
    from .research.designer import (
        design_from_breakthrough,
        design_from_idea,
        design_from_paper,
    )

    prereg = None
    if args.idea:
        prereg = design_from_idea(args.idea, exp_id=args.id)
    elif args.from_paper:
        try:
            _, prereg = design_from_paper(args.from_paper, exp_id=args.id)
        except Exception as e:
            print(f"[design] 从论文设计实验失败：{e}", file=sys.stderr)
            return EXIT_SOURCE
    elif args.breakthrough:
        target = next(
            (a for a in BREAKTHROUGH_ARCHETYPES if a["id"].upper() == args.breakthrough.upper()),
            None,
        )
        if not target:
            valid_ids = [a["id"] for a in BREAKTHROUGH_ARCHETYPES]
            print(f"[validate] 未知突破点 ID：{args.breakthrough}，可选：{valid_ids}",
                  file=sys.stderr)
            return EXIT_ARGS
        bt = Breakthrough(
            id=target["id"],
            title=target["title"],
            category=target["category"],
            target_bottleneck=target["target_bottleneck"],
            theoretical_rationale=target["theoretical_rationale"],
            suggested_method=target["suggested_method"],
            falsifiable_claim=target["falsifiable_claim"],
            minimal_experiment=target["minimal_experiment"],
            confidence="HIGH",
        )
        prereg = design_from_breakthrough(bt, exp_id=args.id)
    else:
        print("[validate] 请提供设计源："
              "--idea \"<构想>\" 或 --from-paper <pdf> 或 --breakthrough <id>",
              file=sys.stderr)
        return EXIT_ARGS

    d = prereg.to_dict()
    problems = validate(d)
    if args.out:
        save(d, args.out)
        print(f"[out] 预注册已写入：{args.out}", file=sys.stderr)

    if args.format == "json":
        print(_json.dumps(d, ensure_ascii=False, indent=2))
    else:
        print(prereg_markdown(d, problems))

    return EXIT_OK if not problems else EXIT_VALIDATION


def _cmd_diagnose(args) -> int:
    """综合学术与数值体检：一键完成槽位提取、缺陷对拍、突破挖掘与实验推荐。"""
    import json as _json

    from .research.diagnose import diagnose_paper, dossier_markdown

    paper_path = args.path
    if not paper_path:
        print("[validate] 请指定待体检的论文路径：--path <paper.pdf>", file=sys.stderr)
        return EXIT_ARGS

    try:
        dossier = diagnose_paper(
            paper_path,
            purpose=args.purpose,
            auto_design=not args.no_design,
            library_path=args.library,
            rules_path=args.rules,
        )
    except FileNotFoundError as e:
        print(f"[source] 文件不存在：{e}", file=sys.stderr)
        return EXIT_SOURCE
    except Exception as e:
        print(f"[diagnose] 体检失败：{e}", file=sys.stderr)
        return EXIT_SOURCE

    if args.format == "json":
        print(_json.dumps(dossier.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(dossier_markdown(dossier))

    if args.out:
        out_p = Path(args.out)
        out_content = (
            _json.dumps(dossier.to_dict(), ensure_ascii=False, indent=2)
            if out_p.suffix == ".json"
            else dossier_markdown(dossier)
        )
        out_p.write_text(out_content, encoding="utf-8")
        print(f"[out] {args.out}", file=sys.stderr)

    return EXIT_OK


def _cmd_reflect(args) -> int:
    """自愈反思：对被推翻或混淆的实验反向计算科学损失梯度并输出自愈建议。"""
    import json as _json

    from .exp.reflect import reflect_on_results, reflection_markdown

    if not args.prereg or not args.results:
        print("[validate] 必须同时指定 --prereg <prereg.json> 和 --results <results.json>",
              file=sys.stderr)
        return EXIT_ARGS

    try:
        diag = reflect_on_results(args.prereg, args.results)
    except FileNotFoundError as e:
        print(f"[source] {e}", file=sys.stderr)
        return EXIT_SOURCE
    except Exception as e:
        print(f"[reflect] 反思分析失败：{e}", file=sys.stderr)
        return EXIT_VALIDATION

    if args.format == "json":
        print(_json.dumps(diag.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(reflection_markdown(diag))

    if args.out:
        out_p = Path(args.out)
        out_content = (
            _json.dumps(diag.to_dict(), ensure_ascii=False, indent=2)
            if out_p.suffix == ".json"
            else reflection_markdown(diag)
        )
        out_p.write_text(out_content, encoding="utf-8")
        print(f"[out] {args.out}", file=sys.stderr)

    # 若有推翻项，保持语义退出码 5 提示有假说被推翻；若全过或仅混淆则返回 0
    return EXIT_REFUTED if diag.refuted_count > 0 else EXIT_OK


def _cmd_evaluate(args) -> int:
    """规则体检：用人工标注的 gold set 算各检测项的精确率/召回率。"""
    from .eval.prf import (
        _load_texts,
        evaluate_items,
        load_corpus,
        load_goldset,
        prf_json,
        prf_markdown,
    )
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
    from .compare.matrix import REPORT_ITEMS, re_search
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
    from .compare.matrix import build_matrix, defect_stats
    from .gaps.mine import gaps_json, gaps_markdown, mine_gaps
    from .match.patterns import load_library, load_rules, match_all
    from .sources.arxiv import RateLimiter, collect
    from .sources.fulltext import load_manifest
    from .sources.local import load_paths

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
    return EXIT_OK if any(g.strength == "STRONG" for g in gaps) else EXIT_NO_STRONG


def _cmd_topics(args) -> int:
    """主题空间审计：哪些方向有、哪些方向一篇都没有。

    与 `coverage` 的分工：coverage 查**给定术语**的覆盖，
    topics 查**主题空间**的分布与空白。两者都补 `gaps` 看不见的那一半 ——
    `gaps` 只看报告规范缺失（报不报 Δt、报不报分辨率）。

    ⚠ 词层面匹配，词义未核验：每条命中都附原文片段，分诊看片段不看计数。
    """
    from .audit.topics import audit_topics, report_markdown
    from .sources.fulltext import load_manifest
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
    from .audit.coverage import audit_coverage, report_markdown
    from .sources.fulltext import load_manifest
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
    return EXIT_OK if not rep.by_verdict("NO_COVERAGE") else EXIT_NO_COVERAGE


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
    print(f"缺陷库：{len(lib)} 条；检测规则：{len(rule_ids)} 条\n")
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
        print(f"\n规则引用了库中不存在的缺陷：{missing_rules}")
    if missing_lib:
        print("\n以下缺陷【无】可执行检测规则（该做而没做，R1 风险）：")
        for i in missing_lib:
            print(f"  - {i}")
    else:
        print("\n✔ 所有文本可检测的缺陷都已有可执行规则。")
    if runtime_only:
        print(f"\n以下 {len(runtime_only)} 条是**运行时缺陷**，判据在代码执行里，论文文本查不到：")
        for i in runtime_only:
            print("  - {}  {}".format(i, by_id.get(i, {}).get("runtime_reason", "")))
    return EXIT_OK


def _cmd_doctor(args) -> int:
    """环境体检：包数据完整性（致命）/ 编码 / PDF 后端 / 缓存（提示）；--net 才联网。"""
    from .doctor import run as doctor_run
    return doctor_run(include_net=args.net, as_json=args.json)


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


def _cmd_install_skill(args) -> int:
    import subprocess
    import sys
    from pathlib import Path

    script_path = Path(__file__).resolve().parents[1] / "scripts" / "install_skill.py"
    if not script_path.exists():
        print(f"[install] 找不到安装脚本：{script_path}", file=sys.stderr)
        return EXIT_VALIDATION

    cmd = [sys.executable, str(script_path), "--workspace", args.workspace, "--host", args.host]
    if getattr(args, "is_global", False):
        cmd.append("--global")
    res = subprocess.run(cmd)
    return res.returncode


def main(argv: list[str] | None = None) -> int:
    # Windows 的控制台/管道默认跟随 locale（中文系统是 GBK/cp936），而本 CLI 的
    # 输出含 ✔/✖/🟡/² 等字符 —— 宿主 harness 用 subprocess 捕获输出时，第一条
    # print 就会 UnicodeEncodeError，退出码 1 且只剩 traceback，看起来像
    # 「校验失败」。这是「挂到任意 harness」的第一道门：stdout/stderr 强制
    # UTF-8，编不出的字符替换掉，保证任何管道里都是合法输出。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):    # 非 TextIO 包装（如测试捕获）或旧运行时
            pass
    from . import __version__
    p = argparse.ArgumentParser(
        prog="fieldmate",
        description="fieldmate：相场/几何处理科研助手（读文献→找精进点→定实验→核结果）")
    p.add_argument("--version", action="version", version=f"fieldmate {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    hv = sub.add_parser("harvest", help="自动语料构建：检索 + 相关性闸门 + 下载全文")
    hv.add_argument("--per-query", type=int, default=10, dest="per_query",
                    help="每条检索式最多抓取篇数")
    hv.add_argument("--only", default="high", choices=["high", "all"],
                    help="high=只用验证过的高纯度检索式（默认）；all=含被污染的检索式")
    hv.add_argument("--no-term-gate", action="store_true",
                    help="关掉词法闸门（只保留学科闸门）—— 用来测闸门的贡献")
    hv.add_argument("--no-download", action="store_true", help="只抓取不下全文")
    hv.add_argument("--llm-cmd", dest="llm_cmd", default=None,
                    help="可选闸门 C：LLM 子领域过滤（判断进 rejection log，可复核）")
    hv.add_argument("--interval", type=float, default=3.0, help="arXiv 请求间隔（秒）")
    hv.add_argument("--out", default=None)
    hv.add_argument("--format", choices=["markdown", "json"], default="markdown")
    hv.set_defaults(func=_cmd_harvest)

    lsub = sub.add_parser("list", help="列出缺陷库、规则覆盖与语料来源")
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
    g.add_argument("--query", help="arXiv 检索式（含空格/引号建议改用 --query-file）")
    g.add_argument("--query-file", dest="query_file", help="从文件读检索式，绕开 shell 拆词")
    g.add_argument("--path", nargs="*", default=None, help="本地 PDF 或目录，与 --corpus 并集")
    g.add_argument("--corpus", default=None,
                   help="离线批量语料 manifest（fieldmate/libraries/corpus_bulk.json 等）；"
                        "与 --query 是并集：检索给摘要、缓存给全文")
    g.add_argument("--limit", type=int, default=30, help="arXiv 检索最多取多少篇")
    g.add_argument("--interval", type=float, default=3.0, help="arXiv 请求间隔（秒，官方要求>=3）")
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
    rd.add_argument("--per-slot", type=int, default=4, dest="per_slot", help="每槽最多候选句数")
    rd.add_argument("--refine-assumptions", action="store_true",
                    help="判断层：用 --llm-cmd 对 Assumption 槽做候选精筛（幻觉闸门在脚本侧）")
    rd.add_argument("--llm-cmd", dest="llm_cmd", default=None,
                    help="宿主 LLM 命令（stdin/stdout JSON 协议，见 docs/DESIGN.md；不填则纯脚本）")
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

    bt = sub.add_parser("breakthrough", help="从旧论文挖掘学术/数值瓶颈与下一步突破点")
    bt.add_argument("--path", required=True, help="待分析的目标论文 PDF 路径")
    bt.add_argument("--format", choices=["markdown", "json"], default="markdown")
    bt.add_argument("--out", help="将突破点分析输出为 JSON 文件")
    bt.set_defaults(func=_cmd_breakthrough)

    ds = sub.add_parser("design", help="根据用户构想或突破点自动设计可证伪预注册实验")
    ds.add_argument("--idea", help="用户的研究设想（如：'我想用凸分裂解决显式步长太小的问题'）")
    ds.add_argument("--from-paper", help="从指定论文挖掘突破点并直接生成实验方案")
    ds.add_argument("--breakthrough", help="指定突破点原型 ID（如 BT-STABILITY-CONVEX）")
    ds.add_argument("--id", help="指定生成的预注册实验 ID")
    ds.add_argument("--format", choices=["markdown", "json"], default="markdown")
    ds.add_argument("--out", help="预注册 JSON 输出路径")
    ds.set_defaults(func=_cmd_design)

    dg = sub.add_parser("diagnose", help="论文综合体检：槽位提取+缺陷对拍+突破挖掘+实验设计闭环")
    dg.add_argument("--path", required=True, help="待分析的目标论文 PDF 路径")
    dg.add_argument(
        "--purpose",
        choices=["implement", "beat", "cite", "build-on"],
        default="beat",
        help="阅读目的",
    )
    dg.add_argument("--no-design", action="store_true", help="跳过自动推荐实验方案步骤")
    dg.add_argument("--library", help="指定缺陷库路径")
    dg.add_argument("--rules", help="指定规则库路径")
    dg.add_argument("--format", choices=["markdown", "json"], default="markdown")
    dg.add_argument("--out", help="将综合体检报告输出至文件")
    dg.set_defaults(func=_cmd_diagnose)

    rf = sub.add_parser("reflect", help="实验自愈反思：对核验失败结果计算科学梯度并输出补丁")
    rf.add_argument("--prereg", required=True, help="预注册 json 路径")
    rf.add_argument("--results", required=True, help="实测结果 json 路径")
    rf.add_argument("--format", choices=["markdown", "json"], default="markdown")
    rf.add_argument("--out", help="将自愈反思报告输出至文件")
    rf.set_defaults(func=_cmd_reflect)

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

    dc = sub.add_parser("doctor", help="环境体检：包数据/编码/PDF 后端/缓存；--net 探测 arXiv")
    dc.add_argument("--net", action="store_true", help="联网探测 arXiv 可达性（默认纯离线）")
    dc.add_argument("--json", action="store_true")
    dc.set_defaults(func=_cmd_doctor)

    s = sub.add_parser("sources", help="体检数据源（arXiv / 本地 PDF）")
    s.add_argument("--query")
    s.add_argument("--query-file", dest="query_file")
    s.add_argument("--path", nargs="*", default=None)
    s.add_argument("--limit", type=int, default=5)
    s.set_defaults(func=_cmd_sources)

    isk = sub.add_parser("install-skill",
                         help="一键挂载 fieldmate skill 到各种 Agent Harness 工作区")
    isk.add_argument("--workspace", default=".", help="目标工作区目录（默认当前目录）")
    isk.add_argument("--host",
                     choices=["agents", "antigravity", "claude", "codex", "cursor", "zcode"],
                     default="zcode", help="宿主 Harness 类型（默认 zcode）")
    isk.add_argument("--global", dest="is_global", action="store_true",
                     help="挂载到当前用户全局 Agent Harness 配置目录")
    isk.set_defaults(func=_cmd_install_skill)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
