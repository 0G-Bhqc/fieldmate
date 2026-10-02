"""测试：守住三件开发期真实踩过的事

1. **「没看过」不能判成「未命中」**（D-REP-001 假阳性事故）
2. **「未出现」只有在「确实看过」时才是信号**
3. **最近邻不能把自己当邻居**
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from fieldmate.compare.matrix import REPORT_ITEMS, build_matrix, defect_stats, matrix_markdown
from fieldmate.match.patterns import load_library, load_rules, match_paper
from fieldmate.sources.arxiv import Paper

ROOT = Path(__file__).resolve().parents[1]


def _paper(**kw) -> Paper:
    d = dict(id="test.0001", title="t", abstract="a", categories=["cs.CE"],
             published="2026-01-01")
    d.update(kw)
    return Paper(**d)


# ------------------------------------------------------------ 库与规则
def test_library_and_rules_load():
    lib = load_library()
    rules = load_rules()
    assert len(lib) >= 15
    assert len(rules["rules"]) >= 10
    lib_ids = {d["id"] for d in lib}
    for r in rules["rules"]:
        assert r["id"] in lib_ids, f"规则引用了库中不存在的缺陷 {r['id']}"


def test_every_defect_has_detection_and_evidence():
    for d in load_library():
        assert d.get("detection", "").strip(), f"{d['id']} 缺 detection"
        assert d.get("evidence", "").strip(), f"{d['id']} 缺 evidence"
        assert d.get("applies_to"), f"{d['id']} 缺 applies_to"


def test_alert_items_reference_known_gap_items():
    """alert_items 里的名字必须是 gaps 模块真正会产出的条目名。

    写错一个名字的后果很隐蔽：那条缺陷会静默地关联不上任何精进点，
    而报告里只是「关联缺陷」少了几条，没有任何报错。
    """
    from fieldmate.gaps.mine import GENRE_SENSITIVITY
    known = set(GENRE_SENSITIVITY)
    for d in load_library():
        for it in d.get("alert_items", []) or []:
            assert it in known, f"{d['id']} 的 alert_items 里有未知条目名 {it!r}"


def test_gaps_actually_link_to_defect_library():
    """实测踩过：22 条精进点的 related_defects 全为空。

    缺陷库描述「跑代码时哪里会出错」，精进点描述「论文里什么没报告」，
    两者之间那层连接如果不显式建模，就永远是空的 —— 报告里少了关键依据，
    却没有任何东西会报错。
    """
    from fieldmate.gaps.mine import _related_defects, mine_gaps
    lib = load_library()
    by_id = {d["id"]: d for d in lib}
    linked = {it: _related_defects(by_id, it) for it in
              ("报时间步", "报稳定条件", "报分辨率", "保体积/守恒", "报噪声模型", "开源自复现")}
    empty = [k for k, v in linked.items() if not v]
    assert not empty, f"这些精进点一条缺陷都没挂上：{empty}"
    # 时间步的机理最直接，必须挂上 D-RES-001
    assert "D-RES-001" in linked["报时间步"]


def test_gap_object_carries_related_defects_end_to_end():
    """走完整条 mine_gaps 路径，而不是只测 _related_defects 这个零件。

    上一版测试只测了 helper，构造 Gap 时残留的 `[d["id"] for d in related]`
    就把返回的 id 字符串当字典取下标，TypeError 一直漏到 CLI 才炸。
    单元测试停在它测的那一层，后面的接线错误它看不见。
    """
    from fieldmate.gaps.mine import mine_gaps
    rows = [_Row("时间分数阶", {"报时间步": False}) for _ in range(6)]
    g = mine_gaps(rows, {}, library=load_library(), min_n=5, min_rate=0.5)[0]
    assert g.item == "报时间步"
    assert "D-RES-001" in g.related_defects
    assert g.to_dict()["related_defects"] == g.related_defects


def test_rules_json_is_valid():
    json.loads((ROOT / "contracts" / "detection_rules.json").read_text(encoding="utf-8"))


# ------------------------------------------------ 缺陷库自洽性
# `patterns` 早先把「该写规则而没写」和「按设计就没有文本规则」混在一张列表里。
# 读的人要么给跑代码才看得见的缺陷硬写一条永远不触发的规则（制造假信号），
# 要么整张列表当真话忽略。两个方向都是自伤，所以必须分栏。
def test_runtime_only_defects_are_marked_and_explained():
    """运行时缺陷必须标 detect_by=runtime 并写明理由。

    判据在代码执行里（D-DIF-001 数步数、D-REP-003 看平移量、D-REP-004 看索引复用），
    论文文本里根本查不到 —— 给它们写文本规则只会产出永远不触发的假信号。
    """
    for d in load_library():
        if d.get("detect_by") == "runtime":
            assert d.get("runtime_reason", "").strip(), \
                f"{d['id']} 标了 runtime 却没写理由，读者无从判断该不该补规则"


def test_text_detectable_defects_have_executable_rules():
    """没有标 runtime 的缺陷必须都有可执行规则 —— 这是库自己的入库规矩。

    违反它意味着 `patterns` 报出来的每一条都是欠账，而欠账没人还。
    """
    rid = {r["id"] for r in load_rules()["rules"]}
    missing = [d["id"] for d in load_library()
               if d.get("detect_by") != "runtime" and d["id"] not in rid]
    assert not missing, f"这些缺陷既没规则也没标 runtime：{missing}"


def test_no_rule_references_an_unknown_defect():
    lib_ids = {d["id"] for d in load_library()}
    for r in load_rules()["rules"]:
        assert r["id"] in lib_ids, f"规则引用了库里不存在的缺陷：{r['id']}"


def test_d_eva_004_rule_precision_on_real_phrasing():
    """D-EVA-004：并列比较显式/隐式却没报时间步。

    规则最危险的地方是「只是提到了 implicit 就命中」—— 那会在整份 248 篇
    语料里刷出大量假阳性。所以正例反例都要钉住。
    """
    lib, rules = load_library(), load_rules()

    def hit(text):
        p = _paper(id="t", title="phase-field numerics", abstract="",
                   fulltext="Cahn-Hilliard phase-field model. " + text)
        return any(m.defect_id == "D-EVA-004" for m in match_paper(p, lib, rules)[0])

    for t in ("We compare the explicit and implicit schemes in terms of cost on GPU.",
              "A benchmark of implicit versus explicit time stepping is in Table 2.",
              "The comparison of explicit and implicit integrators shows the trade-off."):
        assert hit(t), f"正例应命中：{t}"
    for t in ("The implicit scheme is used throughout; the explicit variant is reference only.",
              "We employ an implicit method for all simulations reported here.",
              "An explicit Euler step is taken, with the implicit form given in the appendix.",
              "We compare our method with Laplacian smoothing and bilateral filtering."):
        assert not hit(t), f"反例误报：{t}"


# ------------------------------------------- 核心：没看过 != 未命中
def test_fulltext_rule_is_undecidable_without_fulltext():
    """回归测试：D-REP-001 是纯代码缺陷，绝不能从摘要判出来。

    早期版本对空 fulltext 求值 absent 信号，导致 7/7 篇假阳性。
    """
    lib, rules = load_library(), load_rules()
    p = _paper(title="A phase field denoising method",
               abstract="We propose a phase field model for denoising. "
                        "Allen-Cahn equation is integrated in time.")
    hits, und = match_paper(p, lib, rules)
    assert all(m.defect_id != "D-REP-001" for m in hits), "D-REP-001 不该在无全文时被判出"
    assert "D-REP-001" in {u["defect_id"] for u in und}


def test_fulltext_can_only_resolve_defects_never_invent_them():
    """补上全文后：需要全文的规则变为可判定；已提供的信息会**消除**缺陷。

    这条断言方向曾经写反（写成"命中数不应减少"），结果测试失败——
    而失败原因说明规则是对的：全文里写了 "unconditionally stable"，
    D-RES-001 的 absent 信号被满足，缺陷自然被解决。
    正确的性质是「可判定性单调增加、假阳性单调不增」。
    """
    lib, rules = load_library(), load_rules()
    base = _paper(title="Allen-Cahn denoising with phase field",
                  abstract="We study the Allen-Cahn equation for denoising. "
                           "The time step is chosen as 0.05 h^2.")
    h1, u1 = match_paper(base, lib, rules)
    assert "D-RES-001" in {m.defect_id for m in h1}

    fixed = _paper(title=base.title, abstract=base.abstract,
                   fulltext="The scheme is unconditionally stable; we report the "
                            "grid resolution and the code is available on GitHub.")
    h2, u2 = match_paper(fixed, lib, rules)

    # 1) 提供了稳定条件 -> D-RES-001 被解决
    assert "D-RES-001" not in {m.defect_id for m in h2}
    # 2) 需要全文的规则从「无法判定」变成「可判定」
    assert len(u2) < len(u1), "有全文后无法判定项必须减少"
    # 3) 关键：假阳性不能增加（D-REP-001 仍不该被无中生有）
    assert all(m.defect_id != "D-REP-001" for m in h2)


# ------------------------------------------- 相场族应触发的规则
def test_phase_field_paper_hits_time_step_rule_when_stability_absent():
    lib, rules = load_library(), load_rules()
    p = _paper(title="Fast mesh denoising by phase field",
               abstract="We solve the Allen-Cahn equation. The time step "
                        "is set to 0.05 h squared.")
    hits, _ = match_paper(p, lib, rules)
    ids = {m.defect_id for m in hits}
    assert "D-RES-001" in ids, "提到时间步但未提稳定条件 -> 应命中 D-RES-001"


def test_irrelevant_paper_hits_nothing():
    lib, rules = load_library(), load_rules()
    p = _paper(title="A study of protein folding with molecular dynamics",
               abstract="We simulate protein folding using molecular dynamics "
                        "and analyse the free energy landscape.")
    hits, _ = match_paper(p, lib, rules)
    assert hits == [], f"无关论文不应命中：{[m.defect_id for m in hits]}"


# ------------------------------------------- 矩阵与报告
def test_matrix_marks_undecidable_separately():
    lib, rules = load_library(), load_rules()
    papers = [_paper(id="a", title="phase field denoising via Allen-Cahn equation",
                     abstract="We use a time step of 0.05 h^2."),
              _paper(id="b", title="phase field image segmentation",
                     abstract="Cahn-Hilliard for segmentation. Full text follows.",
                     fulltext="We report resolution and stability conditions; "
                              "code is on GitHub; the noise model is Gaussian.")]
    from fieldmate.match.patterns import match_all
    rows = build_matrix(papers, match_all(papers, lib, rules))
    a, b = rows[0], next(r for r in rows if r.paper_id == "b")
    assert a.undecidable, "无全文的论文应有无法判定项"
    assert not b.has_fulltext is False, "b 有全文"


def test_report_includes_all_four_limitations():
    lib, rules = load_library(), load_rules()
    p = _paper(title="phase field denoising with Allen-Cahn equation",
               abstract="time step 0.05 h^2")
    from fieldmate.match.patterns import match_all
    rows = build_matrix([p], match_all([p], lib, rules))
    md = matrix_markdown(rows, defect_stats(rows, lib), "q", "arxiv", lib)
    for phrase in ("arXiv 覆盖不全", "未提及", "可核查性", "假阳性"):
        assert phrase in md, f"报告缺少限制条款：{phrase}"


def test_defect_stats_has_denominator():
    lib, rules = load_library(), load_rules()
    papers = [_paper(id=f"p{i}", title="phase field denoising Allen-Cahn",
                     abstract="time step 0.05 h^2") for i in range(3)]
    from fieldmate.match.patterns import match_all
    rows = build_matrix(papers, match_all(papers, lib, rules))
    st = defect_stats(rows, lib)
    assert st["n_papers"] == 3
    for name, m in st["missing_report_items"].items():
        assert m["total"] == 3, f"{name} 缺分母"
        assert 0.0 <= m["rate"] <= 1.0


# ------------------------------------------------ 规则体检（防回归）
def test_goldset_item_names_match_report_items():
    """回归测试：gold set 里的 item 名必须和 REPORT_ITEMS 对得上。

    曾经把 gold set 写成「保体积守恒」而代码期望「保体积/守恒」，
    结果该项在体检里静默变成「0 样本 / 无用」，看起来像规则失效，
    其实是标注与代码对不上。静默错位是这类工具最难查的一类 bug。
    """
    from fieldmate.compare.matrix import REPORT_ITEMS
    from fieldmate.eval.prf import load_goldset
    names = {n for n, _, _ in REPORT_ITEMS}
    gold_items = {g["item"] for g in load_goldset()}
    assert gold_items <= names, f"gold set 出现未知 item：{gold_items - names}"
    assert gold_items == names, f"有 item 没有标注样本：{names - gold_items}"


def test_all_report_item_regexes_compile():
    """最基础的守卫：每条正则都必须能编译。

    写这条是因为真的踩过：给「报时间步」补真实论文写法时多留了一个右括号，
    整个包直接 SyntaxError，所有子命令全挂。
    """
    import re as _re
    from fieldmate.compare.matrix import REPORT_ITEMS
    for name, pat, did in REPORT_ITEMS:
        _re.compile(pat, _re.IGNORECASE)          # 编译失败会直接抛
        assert did.startswith("D-"), f"{name} 关联缺陷 id 异常：{did}"


# ------------------------------------------------ 自动语料构建（harvest）
def test_domain_gate_rejects_pure_ml_paper():
    """回归测试：ML 主分类且无数学/物理分类的论文必须被拒。

    真实案例：MENO（神经算子）、flow boiling（latent diffusion）、
    crack growth（条件扩散）—— 它们都含 "phase field" 字样，纯关键词闸门拦不住。
    """
    from fieldmate.sources.arxiv import Paper
    from fieldmate.sources.corpus import DomainFilter
    p = Paper(id="x", title="MENO: MeanFlow-Enhanced Neural Operators for "
                             "Dynamical Systems", abstract="We study phase field "
                           "dynamics with neural operators and diffusion models.",
             categories=["cs.LG", "cs.NA"])
    ok, why = DomainFilter().check(p)
    assert not ok and "ML" in why, why


def test_domain_gate_rejects_weak_term_with_ml_flags():
    """弱术语（裸 'phase field'）+ ML 特征词 -> 拒。

    真实案例：Optical Fringe Patterns Filtering（CNN 光栅条纹滤波），
    之前靠 `spatial discretiz` 类的强词误放行过。
    """
    from fieldmate.sources.arxiv import Paper
    from fieldmate.sources.corpus import DomainFilter
    p = Paper(id="y", title="Optical Fringe Patterns Filtering Based on Multi-Stage "
                             "Convolution Network", abstract="We denoise fringe "
                           "patterns; the phase field of the fringe is estimated "
                           "with a deep learning model.",
             categories=["cs.CV"])
    ok, why = DomainFilter().check(p)
    assert not ok, why


def test_domain_gate_accepts_real_phase_field_paper():
    from fieldmate.sources.arxiv import Paper
    from fieldmate.sources.corpus import DomainFilter
    p = Paper(id="z", title="Surface Reconstruction by the Phase-Field Model",
              abstract="We formulate a phase-field method for reconstructing a "
                        "surface from an unorganized point cloud.",
              categories=["math.NA"])
    ok, why = DomainFilter().check(p)
    assert ok and "强术语" in why, why


def test_domain_gate_keeps_time_fractional_mathna():
    from fieldmate.sources.arxiv import Paper
    from fieldmate.sources.corpus import DomainFilter
    p = Paper(id="w", title="A stabilized scheme for the time-fractional Allen-Cahn "
                             "equation", abstract="We analyze convergence order.",
              categories=["math.NA", "math-ph"])
    assert DomainFilter().check(p)[0]


def test_strong_and_weak_terms_are_disjoint():
    from fieldmate.sources.corpus import STRONG_TERMS, WEAK_TERMS
    assert set(STRONG_TERMS) != set(WEAK_TERMS)
    # 裸 'phase[- ]field' 只能出现在弱术语里
    assert r"phase[- ]field" in WEAK_TERMS
    assert r"phase[- ]field" not in STRONG_TERMS


def test_all_subcommands_are_registered():
    """守卫：每个子命令都必须真的挂到 parser 上。

    写这条是因为真的踩过：往 subparser 里插 `read` 时，
    `list` 被整段替换掉了，而它只在 `python -m fieldmate list` 时才暴露。
    """
    import io
    from contextlib import redirect_stdout
    from fieldmate.cli import main
    for cmd in ("list", "patterns", "read", "gaps", "compare", "prereg",
                "verify", "evaluate", "sources", "harvest"):
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                main([cmd, "--help"])
        except SystemExit as e:
            assert e.code == 0, f"{cmd} --help 退出码 {e.code}（该子命令可能没注册）"
        else:
            pytest.fail(f"{cmd} --help 没有退出，argparse 行为异常")


# ------------------------------------------------ 主题空间审计
# 这个坑我连踩三次，每次形态不同，根子是一个：**词频统计不能支撑任何结论**
#   1. 只搜标题 -> differentiable 0 篇 -> 宣称「这条线空的」（全文级其实 52 篇）
#   2. 全文级搜 -> adjoint 42 篇 -> 差点当成 42 篇可微求解器工作
#      实际多数是**泛函分析里的伴随算子**（术语碰撞）
#   3. 搜具名框架 prisms -> 命中 2 篇 -> 实际是 "hexagonal prisms"（Wulff 形状）
#      和 "tetrahedra, prisms, or hexahedra"（网格单元）（子串误匹配）
#
# 所以探针提供两个防碰撞机制 + 必须附原文片段，**不给结论**。

def _topic_paper(pid, title, body_lines, refs=None):
    from fieldmate.sources.arxiv import Paper
    ft = "\n".join(body_lines)
    if refs:
        ft += "\nReferences\n" + "\n".join(refs)
    return Paper(id=pid, title=title, abstract="", fulltext=ft)


def test_topic_probe_requires_all_groups_to_cooccur():
    """共现要求是消解术语碰撞的核心：`adjoint` 单独出现不算数。"""
    from fieldmate.audit.topics import TopicProbe
    probe = TopicProbe("可微求解器", [["adjoint"], ["differentiable solver"]])
    assert probe.all_groups_hit("we use the adjoint method")[0] is False
    assert probe.all_groups_hit("we build a differentiable solver")[0] is False
    ok, found = probe.all_groups_hit("the adjoint of a differentiable solver")
    assert ok is True
    assert "adjoint" in found.values()


def test_topic_probe_rejects_functional_analysis_adjoint():
    """回归：'Duality estimates' 这类纯分析论文不该被算作可微求解器工作。"""
    from fieldmate.audit.topics import TopicProbe
    probe = TopicProbe("可微求解器",
                       [["adjoint"], ["differentiable solver", "gradient-based"]])
    analysis = ("we prove duality estimates for the adjoint operator under a "
                "locally Lipschitz condition and derive sharp bounds")
    assert probe.all_groups_hit(analysis)[0] is False


def test_topic_audit_counts_and_context_are_reported():
    from fieldmate.audit.topics import TopicProbe, audit_topics
    probe = TopicProbe("X", [["adjoint"], ["gradient-based"]])
    papers = [_topic_paper(f"p{i}", "t",
                           ["we build a differentiable solver and its adjoint method "
                            "for gradient-based calibration of the coupling coefficient"])
              for i in range(3)]
    au = audit_topics(papers, [probe])
    assert au.body_count("X") == 3
    h = au.topics[0][1][0]
    assert h.context, "每条命中必须带原文片段，否则人还得逐篇打开 PDF"
    assert "gradient" in h.context.lower() or "adjoint" in h.context.lower()


def test_topic_audit_does_not_multiply_untrimmable_by_probe_count():
    """回归：n_untrim 曾被累加在探针循环**内**，5 个探针把 22 篇报成 110 篇。"""
    from fieldmate.audit.topics import TopicProbe, audit_topics
    probes = [TopicProbe(f"T{i}", [["alpha"], ["beta"]]) for i in range(5)]
    papers = [_topic_paper(f"p{i}", "t", [f"alpha beta line {j}" for j in range(120)])
              for i in range(3)]
    au = audit_topics(papers, probes)
    assert au.n_untrimmable == 3, "计数类的东西不能按探针重复累加"
    assert au.n_with_fulltext == 3


def test_topic_report_states_the_disclaimer():
    """「词层面匹配，词义未核验」必须出现在输出里，否则读者会把计数当结论。"""
    from fieldmate.audit.topics import TopicProbe, audit_topics, report_markdown
    probe = TopicProbe("X", [["alpha"], ["beta"]])
    au = audit_topics([_topic_paper("p", "t", ["alpha and beta here"] + ["pad"] * 130)],
                      [probe])
    md = report_markdown(au)
    assert "词义未核验" in md
    assert "分诊看片段" in md


def test_topic_audit_reports_zero_hits_as_a_gap():
    from fieldmate.audit.topics import TopicProbe, audit_topics
    probe = TopicProbe("空的", [["nonexistent-xyz"], ["also-missing"]])
    papers = [_topic_paper(f"p{i}", "t", [f"padding line {j}" for j in range(120)])
              for i in range(3)]
    au = audit_topics(papers, [probe])
    assert au.body_count("空的") == 0
    from fieldmate.audit.topics import report_markdown
    assert "语料缺口" in report_markdown(au)


# ------------------------------------------------ 适配层（MCP / Skill）
# 「可挂载到任意 harness」这句话，在能力①和③上曾经**不成立**：
# MCP 只接了 compare / patterns / sources 三件，也就是只有「读文献」这一半。
# 宿主挂上去却用不上三分之二的东西。缺入口不会报错，只会让承诺悄悄落空。
def _mcp_server():
    import importlib.util
    p = ROOT / "adapters" / "mcp" / "server.py"
    spec = importlib.util.spec_from_file_location("fieldmate_mcp_adapter", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_mcp_adapter_exposes_all_three_capabilities():
    """三项能力都必须有 MCP 入口：①精进点 ②横向对比 ③实验核验。"""
    mod = _mcp_server()
    for fn in mod.TOOL_NAMES:
        assert callable(getattr(mod, fn, None)), f"{fn} 没导出"
    # 名字对得上也可能是空壳，必须真的能调
    assert callable(mod.mine_gaps_tool), "能力① 精进点没有入口"
    assert callable(mod.compare_papers), "能力② 横向对比没有入口"
    assert callable(mod.verify_result_tool), "能力③ 实验核验没有入口"


def test_mcp_check_sources_without_arguments_reports_why():
    """回归：早先无参返回 `{}` —— 什么都不做、也不说原因的空结果。"""
    import json
    mod = _mcp_server()
    out = json.loads(mod.check_sources())
    assert out["ok"] is False
    assert "请至少指定" in out["error"]


def test_mcp_gaps_tool_runs_on_the_local_corpus():
    import json
    mf = ROOT / "libraries" / "corpus_bulk.json"
    if not mf.exists():
        pytest.skip("目标语料不在本机")
    mod = _mcp_server()
    out = json.loads(mod.mine_gaps_tool(str(mf), min_n=20, fmt="json"))
    assert "gaps" in out
    assert out["n_candidates"] > 0
    assert out["n_distinct_items"] > 0


def test_mcp_verify_tool_reports_missing_files_loudly():
    """不能对不存在的预注册返回空结果 —— 那是「无法判定」被伪装成「没问题」。"""
    import json
    mod = _mcp_server()
    out = json.loads(mod.verify_result_tool("nope.json", "also-nope.json"))
    assert out["ok"] is False and "不存在" in out["error"]


def test_skill_adapter_documents_the_three_capabilities():
    """SKILL.md 是给宿主模型读的入口说明。三项能力缺一个，
    宿主就只会用到它会用的那一半。"""
    txt = (ROOT / "adapters" / "skill" / "SKILL.md").read_text(encoding="utf-8")
    for kw in ("gaps", "compare", "verify"):
        assert kw in txt, f"SKILL.md 没提到 {kw}"


# ------------------------------------------------ 打包完整性
# README 承诺「挂到任意 harness 都能用」，而那基本就是 `pip install` 的用法。
# 这类 bug 极其隐蔽：源码树里一切正常（直接在仓库跑），打包后才炸。
def test_every_subpackage_is_declared_for_packaging():
    """漏一个子包 -> 源码能跑、`pip install` 后 ModuleNotFoundError。

    `fieldmate.audit` 曾漏在 pyproject 的 packages 列表里：本地一切正常，
    打包后 `import fieldmate.audit` 直接失败。
    """
    import tomllib
    pyproj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = set(pyproj["tool"]["setuptools"]["packages"])
    on_disk = {f"fieldmate.{p.name}" for p in (ROOT / "fieldmate").iterdir()
               if p.is_dir() and (p / "__init__.py").exists()}
    missing = on_disk - declared
    assert not missing, f"这些子包没写进 [tool.setuptools].packages：{sorted(missing)}"


def test_declared_packages_all_exist():
    """反向也要查：声明了但目录不存在 -> 安装时 setuptools 直接报错。"""
    import tomllib
    pyproj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for pkg in pyproj["tool"]["setuptools"]["packages"]:
        d = ROOT.joinpath(*pkg.split("."))
        assert d.is_dir(), f"{pkg} 声明了但目录不存在：{d}"
        assert (d / "__init__.py").exists(), f"{pkg} 缺少 __init__.py"


def test_pyproject_declares_no_hard_dependencies():
    """核心承诺是「纯 stdlib 即可跑」。这条依赖一旦被加进来，承诺就破了。"""
    import tomllib
    pyproj = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproj["project"].get("dependencies") == [], \
        "核心依赖必须为空；可选依赖放 [project.optional-dependencies]"
    for extra, deps in pyproj["project"]["optional-dependencies"].items():
        for d in deps:
            assert not d.split(">")[0].split("=")[0].strip().startswith(
                ("openai", "anthropic", "litellm")), \
                f"可选依赖 {extra} 里出现了 LLM SDK：{d}。LLM 是插件不是前置条件。"


def test_console_script_entry_point_is_importable():
    import importlib
    mod, _, fn = "fieldmate.cli:main".partition(":")
    assert callable(getattr(importlib.import_module(mod), fn))


def test_package_is_importable_from_any_working_directory(tmp_path):
    """回归：早先测试只在项目根跑得通，换个目录就 ModuleNotFoundError。

    对外承诺是「挂到任意 harness 都能用」，而 harness 启动子进程时的 cwd
    不会是仓库根 —— 可能是用户 home、skill 目录或临时目录。
    这类故障只报 ModuleNotFoundError，不提示任何原因，极难定位。

    只验「换个目录还能 import + 跑起 CLI」，**不跑整套测试**：
    在子进程里跑全套会再次执行本用例 -> 无限递归。
    （踩过：最初写成 `pytest tests/` 子进程调用，套件直接挂住。）
    """
    import subprocess
    import sys as _sys
    code = (
        "import fieldmate, fieldmate.audit, fieldmate.audit.topics;"
        "from fieldmate.cli import main;"
        "print('ok')"
    )
    r = subprocess.run([_sys.executable, "-c", code],
                       capture_output=True, text=True, cwd=str(tmp_path),
                       env={**os.environ, "PYTHONPATH": str(ROOT)})
    assert r.returncode == 0, f"换个目录就 import 不了：\n{r.stderr[-800:]}"
    assert "ok" in r.stdout


def test_conftest_puts_project_root_on_sys_path():
    """conftest.py 必须存在并真的把仓库根加进 sys.path，否则上面那条只是靠 PYTHONPATH。"""
    cf = ROOT / "conftest.py"
    assert cf.exists(), "缺 conftest.py：测试会依赖 cwd，在 harness 里必然 import 失败"
    src = cf.read_text(encoding="utf-8")
    assert "sys.path" in src and "ROOT" in src


# ------------------------------------------------ Markdown 标题（结构后端路径）
# pymupdf4llm 是**可选**依赖。实测 20 篇：标题总数 85->296，method 检出 3/20->13/20。
# 它给的是 `## 3. Numerical discretization` 这种带 # 的显式结构，
# 于是上面那整套「行首+编号+短行+词表」的启发式补丁可以退居二线。
#
# 这组测试**必须在没装 pymupdf4llm 的环境里通过** —— 零依赖是硬底线。
_MD_LINES = [
    "# A paper title that is not a section heading",
    "## **Abstract**",
    "some abstract text that runs on for a while and should not be cut in half",
    "## **1. Introduction**",
    "intro body line with enough words to survive the sentence length filter",
    "## 3. Numerical discretization for 3D reconstruction",
    "we discretize the operator on a uniform grid with spacing h and step size dt",
    "## 4. Numerical experiments for 3D reconstruction",
    "we set the mesh size by h = 1/Nx and the time step is chosen for stability",
    "## **5. Conclusions**",
    "the method converges and preserves the volume of the phase to within drift",
    "## **References**",
    "[1] Someone. A reference entry. Journal 1, 1-10, 2020.",
]


def test_markdown_headings_are_trusted_without_guessing():
    from fieldmate.extract.slots import _heading_at
    assert _heading_at("## **Abstract**")[0] == "abstract"
    assert _heading_at("## **1. Introduction**")[0] == "intro"
    assert _heading_at("## 3. Numerical discretization for 3D reconstruction")[0] == "method"
    assert _heading_at("## 4. Numerical experiments for 3D reconstruction")[0] == "experiment"
    assert _heading_at("## **5. Conclusions**")[0] == "conclusion"
    assert _heading_at("## **References**")[0] == "ref"


def test_markdown_body_line_with_hash_is_not_a_heading():
    """`#` 必须在行首才算标记；正文里出现 # 不能被当标题。"""
    from fieldmate.extract.slots import _heading_at
    assert _heading_at("this is a body line with a # in the middle") is None


def test_unrecognised_markdown_heading_is_not_forced_into_a_family():
    """带 # 但词表不认 -> 判 None，**不硬塞**。

    塞错族比漏掉更糟：会把「这节其实是实验」记成「这节是方法」，
    污染该节全部候选句的落点统计，而且没有任何东西会报错。
    """
    from fieldmate.extract.slots import _heading_at
    for line in ("## Acknowledgment", "## _4.1. Parameter test_",
                 "# A paper title that is not a section heading"):
        assert _heading_at(line) is None, line


def test_markdown_text_produces_real_sections():
    from fieldmate.extract.slots import _sections
    names = [n for n, _ in _sections("\n".join(_MD_LINES))]
    assert {"abstract", "intro", "method", "experiment"} <= set(names)
    assert "ref" not in names, "参考文献区不应作为章节返回"


def test_markdown_sections_do_not_leak_references():
    from fieldmate.extract.slots import _sections
    body = " ".join(b for _, b in _sections("\n".join(_MD_LINES)))
    assert "Someone" not in body, "参考文献条目不能漏进正文段落"


def test_reference_heading_pattern_accepts_markdown_and_emphasis():
    """pymupdf4llm 输出 `## **References**`，旧正则匹配不到，
    会导致参考文献区裁剪失效 —— 那正是「静默用错口径」的老问题。"""
    from fieldmate.sources.local import find_reference_start
    cut = find_reference_start("\n".join(_MD_LINES))
    assert cut >= 0
    assert _MD_LINES[cut].strip() == "## **References**"


def test_structured_backend_is_off_by_default():
    """零依赖是硬底线：默认不注册 pymupdf4llm。"""
    import fieldmate.sources.local as loc
    assert loc._USE_LLM_BACKEND is False
    assert "pymupdf4llm" not in loc.available_backends()


def test_structured_backend_availability_check_does_not_mutate_state():
    import fieldmate.sources.local as loc
    before = loc._USE_LLM_BACKEND
    loc.structured_backend_available()
    assert loc._USE_LLM_BACKEND == before


def test_enable_structured_backend_clears_registered_extractors():
    """切模式必须清空已注册的后端，否则开关是假的。"""
    import fieldmate.sources.local as loc
    saved = loc._USE_LLM_BACKEND
    try:
        loc._EXTRACTORS.clear()
        loc.enable_structured_backend(True)
        assert loc._USE_LLM_BACKEND is True
        assert loc._EXTRACTORS == [], "切模式后必须重新注册，否则开关无效"
    finally:
        loc._EXTRACTORS.clear()
        loc._USE_LLM_BACKEND = saved


def test_parsed_text_cache_separates_raw_text_from_markdown(tmp_path, monkeypatch):
    """回归：缓存文件名只按 PDF 名字分时，切换后端会读到**上一种**的产物，
    于是「关掉后端」看起来生效（backend 名变成 cache），文本其实还是 Markdown。
    开关变成假开关，而且极难发现。"""
    import fieldmate.sources.local as loc
    saved_mode, saved_ex = loc._USE_LLM_BACKEND, list(loc._EXTRACTORS)
    monkeypatch.chdir(tmp_path)
    pdf = tmp_path / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4 " + b"body " * 400)
    try:
        loc._EXTRACTORS.clear()
        loc._USE_LLM_BACKEND = False
        loc._EXTRACTORS.append(("fake", lambda p: "## 1. Introduction\nbody text here"))
        _, miss1 = loc.parse_pdf_cached(pdf)        # 未命中 -> 真实后端名
        t_raw, hit1 = loc.parse_pdf_cached(pdf)     # 命中 -> "cache"
        loc._EXTRACTORS.clear()
        loc._USE_LLM_BACKEND = True
        loc._EXTRACTORS.append(("fake", lambda p: "# different\nmore body"))
        _, miss2 = loc.parse_pdf_cached(pdf)
        t_md, hit2 = loc.parse_pdf_cached(pdf)
        assert (miss1, hit1) == ("fake", "cache")
        assert (miss2, hit2) == ("fake", "cache")
        assert t_raw.startswith("## 1. Introduction")
        assert t_md.startswith("# different"), \
            "切到 md 模式后若读到的是上一次的产物，说明缓存键没带模式"
        cdir = loc._text_cache_dir()
        assert (cdir / "sample.txt.txt").exists()
        assert (cdir / "sample.md.txt").exists(), \
            "两种模式必须落到不同缓存文件，否则切换后端会读到上一次的产物"
    finally:
        loc._EXTRACTORS.clear()
        loc._EXTRACTORS.extend(saved_ex)
        loc._USE_LLM_BACKEND = saved_mode


# ------------------------------------------------ 五槽抽取与阅读卡（能力②）
def test_mention_of_references_in_prose_is_not_the_reference_section():
    """正文里的 "see the references [3]" 不能被当成参考文献区起点。"""
    from fieldmate.sources.local import find_reference_start
    body = "\n".join(f"body line {i} discussing the method in detail" for i in range(200))
    text = body + "\nsee the references [3] for the original argument\n" + \
        "\n".join(f"more body {i}" for i in range(50))
    assert find_reference_start(text) == -1


def test_reference_section_is_found_and_body_excludes_it():
    from fieldmate.sources.local import body_without_references, find_reference_start
    body = "\n".join(f"body line {i} with real content" for i in range(120))
    refs = "References\n[1] Someone. A paper title here. Journal 1, 1-10, 2020."
    text = body + "\n" + refs
    cut = find_reference_start(text)
    assert cut == 120
    out, ok = body_without_references(text)
    assert ok is True
    assert "References" not in out and "Someone" not in out
    assert "body line 0" in out


def test_unlocatable_reference_section_returns_original_text():
    """裁不掉时必须原样返回并置 False —— 静默砍掉一半正文比留着参考文献严重得多。"""
    from fieldmate.sources.local import body_without_references
    text = "\n".join(f"line {i} no heading anywhere" for i in range(200))
    out, ok = body_without_references(text)
    assert ok is False
    assert out == text


def test_body_without_references_never_returns_almost_nothing():
    """参考文献区占全文 90% 以上时判为裁剪失败，不把剩下那点当正文交出去。"""
    from fieldmate.sources.local import body_without_references
    text = "intro\nReferences\n" + "\n".join(f"[{i}] ref entry" for i in range(500))
    out, ok = body_without_references(text)
    assert ok is False
    assert out == text


def test_report_items_are_matched_on_body_not_on_bibliography():
    """核心回归：引用别人报的 Δt，不等于这篇自己报了。

    `build_matrix` 早先拿整份 PDF 文本（含参考文献）匹配 REPORT_ITEMS，
    于是「报得越少、缺得越不明显」。实测 247 篇上这项偏差 0.4~3.6pp，
    9% 的篇目根本裁不掉参考文献区 —— 口径不齐比偏差本身更糟。
    """
    from fieldmate.compare.matrix import REPORT_ITEMS, build_matrix
    from fieldmate.sources.arxiv import Paper
    body = "\n".join(f"we describe the method in line {i}" for i in range(120))
    text = (body + "\nReferences\n[1] X. Time step size selection. J. 2020.\n"
            "    we take N = Nx = Ny = Nz = 128 and h = (b-a)/N for the mesh.")
    p = Paper(id="t1", title="t", abstract="", fulltext=text)
    rows = build_matrix([p], {})
    r = rows[0]
    assert r.refs_trimmed is True
    for name, _pat, _d in REPORT_ITEMS:
        assert r.reported[name] is False, f"{name} 被参考文献区里的内容误判为已报告"


def test_matrix_row_discloses_reference_trim_status():
    from fieldmate.compare.matrix import MatrixRow
    assert MatrixRow(paper_id="x", year=None, title="t", venue="", family="f",
                     reported={}, defects=[]).refs_trimmed is False
    assert "refs_trimmed" in MatrixRow(paper_id="x", year=None, title="t", venue="",
                                       family="f", reported={}, defects=[]).to_dict()


def test_defect_stats_reports_reference_scope():
    """口径必须可披露：多少篇裁掉了、多少篇没裁，不能让读者默认全都裁过。"""
    from fieldmate.compare.matrix import MatrixRow, defect_stats
    rows = [MatrixRow(paper_id=f"p{i}", year=2026, title="t", venue="", family="f",
                      reported={name: True for name, _p, _d in REPORT_ITEMS},
                      defects=[], has_fulltext=True, refs_trimmed=(i == 0))
            for i in range(3)]
    st = defect_stats(rows, [])
    assert st["reference_scope"]["n_with_fulltext"] == 3
    assert st["reference_scope"]["n_refs_trimmed"] == 1
    assert st["reference_scope"]["n_refs_not_trimmed"] == 2


# ------------------------------------------------ 语料覆盖审计
# 为什么要单独数「命中出处」
# ------------------------
# 实测：Langevin 在 247 篇全文里词面命中 11 篇，逐篇打开核实后真正相关只有 2~3 篇
# —— 其余是参考文献里出现过该短语、统计物理的 Holstein/自旋模型、以及机器学习的
# Riemannian Langevin MCMC。**「命中 11 篇」是个误导性指标。**
# 只在参考文献区出现的命中，几乎肯定是「引用了别人」，不该计入可对比篇数。

def _cov_paper(pid, title, body_lines, ref_lines=None):
    from fieldmate.sources.arxiv import Paper
    body = "\n".join(body_lines)
    ft = body
    if ref_lines:
        ft += "\nReferences\n" + "\n".join(ref_lines)
    return Paper(id=pid, title=title, abstract="", fulltext=ft)


def test_term_in_title_counts_as_strong_coverage():
    from fieldmate.audit.coverage import TermSpec, audit_coverage
    papers = [_cov_paper(f"p{i}", "Langevin driven Cahn-Hilliard flow",
                         [f"body line {j}" for j in range(120)]) for i in range(6)]
    t = audit_coverage(papers, [TermSpec("Langevin")]).terms[0]
    assert t.n_title == 6 and t.n_strong == 6 and t.verdict == "OK"


def test_term_only_in_bibliography_is_not_counted_as_comparable():
    from fieldmate.audit.coverage import TermSpec, audit_coverage
    p = _cov_paper("p1", "Some title",
                   [f"body line {i}" for i in range(120)],
                   ref_lines=["[1] X. The Langevin equation of motion. J. 2020."])
    t = audit_coverage([p], [TermSpec("Langevin")]).terms[0]
    assert t.n_strong == 0
    assert t.n_ref_only == 1, "只在参考文献区出现 -> 不计入可对比篇数"
    assert t.n_any == 1
    assert t.verdict == "NO_COVERAGE", "一篇只能引用的论文不算有支撑"


def test_term_in_body_counts_as_strong_coverage():
    from fieldmate.audit.coverage import TermSpec, audit_coverage
    p = _cov_paper("p1", "t", ["we derive the Langevin equation of motion here."] +
                   [f"line {i}" for i in range(120)])
    t = audit_coverage([p], [TermSpec("Langevin")]).terms[0]
    assert t.n_body == 1 and t.n_strong == 1


def test_term_matching_tolerates_hyphen_space_and_underscore():
    from fieldmate.audit.coverage import TermSpec, audit_coverage
    papers = [_cov_paper(f"p{i}", "t", [f"we use the phase_field model here {i}"] +
                         [f"line {j}" for j in range(120)]) for i in range(3)]
    t = audit_coverage(papers, [TermSpec("phase field")]).terms[0]
    assert t.n_body == 3, "phase_field / phase field / phase-field 必须等价"


def test_thin_coverage_is_flagged_below_threshold():
    from fieldmate.audit.coverage import TermSpec, audit_coverage
    papers = [_cov_paper(f"p{i}", "t", ["we use the modified Allen-Cahn flow here"] +
                         [f"line {j}" for j in range(120)]) for i in range(2)]
    t = audit_coverage(papers, [TermSpec("modified Allen-Cahn")]).terms[0]
    assert t.verdict == "THIN", "少于阈值的覆盖要做横向对比，统计功效不足"


def test_coverage_report_separates_strong_from_reference_only():
    from fieldmate.audit.coverage import TermSpec, audit_coverage
    spec = TermSpec("Langevin")
    strong = [_cov_paper(f"s{i}", "Langevin in practice",
                         ["we use the Langevin equation."] + [f"l{j}" for j in range(120)])
              for i in range(6)]
    weak = [_cov_paper(f"w{i}", "t", [f"l{j}" for j in range(120)],
                       ref_lines=["[1] Y. Langevin equation theory. 2020."])
            for i in range(4)]
    rep = audit_coverage(strong + weak, [spec])
    t = rep.terms[0]
    assert t.n_strong == 6 and t.n_ref_only == 4 and t.n_any == 10
    assert t.verdict == "OK"
    assert rep.to_dict()["ok"] == ["Langevin"]


def test_coverage_report_carries_the_asymmetry_caveat():
    """「0 命中是强告警，非 0 命中不证明相关」这条不对称必须写在输出里。"""
    from fieldmate.audit.coverage import TermSpec, audit_coverage
    rep = audit_coverage([], [TermSpec("x")])
    d = rep.to_dict()
    assert "0 命中是强告警" in d["caveat"]
    assert "n_ref_only" in d["caveat"]
    assert d["no_coverage"] == ["x"]


def test_papers_without_fulltext_are_not_counted():
    from fieldmate.audit.coverage import TermSpec, audit_coverage
    from fieldmate.sources.arxiv import Paper
    rep = audit_coverage([Paper(id="a", title="t", abstract="x", fulltext=None)],
                         [TermSpec("x")])
    assert rep.n_with_fulltext == 0
    assert rep.terms[0].n_strong == 0

    """章节切分覆盖不到全文 30% 时必须退回全文扫描。

    回归测试：章节正则先后在三篇真实 PDF 上摆了三次（过松 -> 过严 -> 退化保护），
    最终结论是「正则不可能对所有刊成立」。因此正确做法不是继续调参，
    而是**检测失败并退化** —— 槽位匹配只依赖句法标记，本来就不需要章节。
    """
    from fieldmate.extract.slots import _sections
    body = "\n".join(f"filler line {i} with some text to pad the body" for i in range(200))
    secs = _sections(body)                      # 无任何可识别标题
    assert len(secs) == 1 and len(secs[0][1]) == len(body)


# ------------------------------------------------ 章节标题识别
# 实测代价：method 章节原先只在 29/247（12%）的论文里检出。
# 根因不是正则太严，而是**方向搞反了** —— PyMuPDF 把「标题 + 首段正文」
# 合并成同一行，而旧实现要求整行只有标题。行首关键词实际能覆盖 246/247（100%）。
# 下面这组全部取自真实 PDF 的原始行。

def test_heading_recognised_when_merged_with_body_on_same_line():
    from fieldmate.extract.slots import _heading_at
    assert _heading_at("1. Introduction. In this paper, we focus on numerical study") \
        is not None
    assert _heading_at("4. Numerical results. In this section, we present experiments") \
        is not None
    assert _heading_at("Abstract. As a variational phase-field model, the TFAC equation") \
        is not None


def test_heading_offset_skips_the_heading_and_keeps_the_body():
    """偏移必须落在标题之后，正文一个字符都不能丢。"""
    from fieldmate.extract.slots import _heading_at
    line = "1. Introduction. In this paper, we focus on numerical study"
    name, off = _heading_at(line)
    assert name == "intro"
    assert line[off:].strip().startswith("In this paper")


def test_prose_starting_with_a_heading_word_is_not_a_heading():
    """PDF 换行把句子切断，续行恰好以 algorithm / experiment / proposed method 开头。

    第一版诊断把这些全判成「0 假阳性」，因为它只把「关键词后直接跟小写词」
    当假阳性 —— 而这些续句首字母是大写的。教训：诊断本身的判据也得被验证。
    """
    from fieldmate.extract.slots import _heading_at
    for line in ("proposed model. Statistic metrics such as the sigma, dmax, dmean",
                 "algorithm based on a modified AC equation. The modified equation used",
                 "experiment in this paper, we take the same parameters adopted in 4.1",
                 "proposed method performs better than the other two algorithms. In future"):
        assert _heading_at(line) is None, f"正文续行被误判为标题：{line[:40]}"


def test_numbered_heading_is_trusted_even_with_unsafe_keyword():
    from fieldmate.extract.slots import _heading_at
    assert _heading_at("3. Numerical discretization for 3D reconstruction") is not None
    assert _heading_at("4. Numerical experiments for 3D reconstruction") is not None


def test_body_line_starting_with_a_number_is_not_a_heading():
    """首版把 method 命中 17 次，就是因为没守住「关键词紧跟编号」这条。"""
    from fieldmate.extract.slots import _heading_at
    assert _heading_at("2. The grid sizes are h = 1/N and zero boundary values") is None
    assert _heading_at("3.1 Convergence analysis is performed on three meshes") is None


def test_standalone_headings_still_recognised():
    from fieldmate.extract.slots import _heading_at
    for line, name in (("Abstract", "abstract"), ("References", "ref"),
                       ("1. Introduction", "intro"), ("5. Conclusions", "conclusion"),
                       ("I. INTRODUCTION", "intro"), ("2. Related Work", "related")):
        got = _heading_at(line)
        assert got is not None and got[0] == name, f"{line!r} -> {got}"


def test_merged_body_is_not_duplicated_into_section_text():
    """标题与正文同行时，同行正文只应出现一次。

    重复拼接 + \\n 分隔 = 同一段话出现两次并被腰斩，实测把
    "mesh size by h = 1/Nx" 劈成 "...mesh" / "size by h = 1/Nx"。
    """
    from fieldmate.extract.slots import _sections
    filler = "\n".join(f"body sentence number {i} padding the section" for i in range(40))
    text = f"1. Introduction. In this paper we study the problem carefully.\n{filler}"
    secs = _sections(text)
    body = " ".join(b for _, b in secs)
    assert body.count("In this paper we study the problem carefully.") == 1


def test_slot_degrades_to_fulltext_when_its_section_is_missing():
    """Mechanism 只认 method；没识别出 method 时必须退化为全文扫描，而不是交白卷。

    退化只放宽搜索范围，不放宽判据 —— 不允许因此凭空多出候选句。
    """
    from fieldmate.extract.slots import extract_slots
    from fieldmate.sources.arxiv import Paper
    text = "\n".join(
        ["1. Introduction. We consider the following formulation in detail."] +
        [f"padding line {i} to make the body long enough" for i in range(80)] +
        ["The scheme is solved by the Crank-Nicolson method.",
         "The discrete operator is given by the standard second difference, and the",
         "resulting linear system is solved implicitly at each time step."])
    ps = extract_slots(Paper(id="x", title="t", abstract=""), text)
    assert "method" not in ps.sections
    assert "Mechanism" in ps.degraded_slots, "缺 method 章节时 Mechanism 必须报出退化"
    got = " ".join(i["sentence"] for i in ps.slots["Mechanism"].items)
    assert "given by" in got, f"退化后应能在全文抓到机制句，实得：{got[:120]}"


def test_degraded_slot_is_disclosed():
    """退化必须可观测，否则「抓到了 N 条」会被误当成「方法段里明确写了 N 条」。"""
    from fieldmate.extract.slots import extract_slots
    from fieldmate.sources.arxiv import Paper
    text = "\n".join(["2. Related Work. Several authors have studied this problem."] +
                     [f"padding {i}" for i in range(80)])
    ps = extract_slots(Paper(id="x", title="t", abstract=""), text)
    assert ps.degraded_slots
    assert "degraded_slots" in ps.to_dict()


def test_slots_finds_protocol_sentences_in_real_paper():
    from fieldmate.extract.slots import extract_slots
    from fieldmate.sources.arxiv import Paper
    from fieldmate.sources.local import parse_pdf
    from fieldmate.eval.prf import load_corpus
    p1 = (load_corpus().get("papers") or {}).get("P1-recon")
    if not p1 or not Path(p1).exists():
        pytest.skip("目标论文不在本机")
    text, _ = parse_pdf(p1)
    if not text:
        pytest.skip("无法解析该 PDF")
    ps = extract_slots(Paper(id="P1", title="t", abstract=""), text, per_slot=6)
    blob = " ".join(i["sentence"] for i in ps.slots["Protocol"].items).lower()
    assert "mesh size" in blob or "time step" in blob or "neumann" in blob, \
        f"Protocol 槽应抓到 h/Δt/边界条件，实得：{blob[:160]}"


def test_reading_card_escapes_pipes_in_markdown():
    """markdown 表格单元格必须转义竖线 —— 假设句里到处是 |x| < |y|。"""
    from fieldmate.extract.disclose import card_markdown
    md = card_markdown([{"paper_id": "p", "title": "t", "n_chars": 10,
                         "sections": [], "claim": None, "slot_counts": {},
                         "top_assumption": "|drift(a)| < |drift(b)|",
                         "top_gap": None, "status": "ok"}], purpose="beat")
    assert r"\|drift(a)\|" in md and "| |drift(a)| < |" not in md


def test_purpose_focus_changes_which_slots_expand():
    from fieldmate.extract.disclose import PURPOSE_FOCUS
    assert "Assumption" in PURPOSE_FOCUS["beat"]
    assert "Mechanism" in PURPOSE_FOCUS["implement"]
    assert PURPOSE_FOCUS["cite"] == ("Claim",)


# ------------------------------------------------ 预注册与核验（能力③）
def _tiny_prereg(**kw):
    d = {
        "id": "t-1", "created": "2026-01-01T00:00:00Z",
        "claim": "相场在保体积上优于拉普拉斯",
        "hypotheses": [{
            "id": "H1", "statement": "|drift(a)| < |drift(b)|", "metric": "drift",
            "compare": "a vs b", "expected": "pf_smaller",
            "falsification": "若 |drift(a)| >= |drift(b)|，H1 被推翻",
            "support_required": []}],
        "confounds": ["分辨率"],
    }
    d.update(kw)
    return d


def test_prereg_rejects_non_falsifiable_claim():
    from fieldmate.exp.prereg import validate
    bad = _tiny_prereg()
    bad["hypotheses"][0]["falsification"] = "我们看看相场是不是更保体积"
    p = validate(bad)
    assert any("认输措辞" in x for x in p), p


def test_prereg_requires_res_floor_for_precision_claims():
    from fieldmate.exp.prereg import validate
    bad = _tiny_prereg()
    bad["hypotheses"][0].update(
        {"statement": "相场的误差更小", "metric": "err", "falsification": "否则 H1 被推翻"})
    p = validate(bad)
    assert any("res_floor" in x for x in p), p


def test_prereg_rejects_missing_iso_created():
    from fieldmate.exp.prereg import validate
    p = validate(_tiny_prereg(created="昨天"))
    assert any("ISO8601" in x for x in p), p


def test_verify_compares_absolute_values_when_statement_uses_bars():
    """回归测试：假设写 |a| < |b|，指标给带符号值时必须按绝对值比。

    否则会拿 +0.05 和 -0.03 比大小，结论直接反掉——
    这个 bug 曾让我把「拉普拉斯体积漂移更小」误读成「相场更小」。
    """
    from fieldmate.exp.verify import verify
    rows = [{"solver": "a", "drift": 0.05}, {"solver": "b", "drift": -0.03}]
    v = verify(_tiny_prereg(), rows, {})
    assert v.hypotheses[0].verdict == "REFUTED", "0.05 > 0.03，应为推翻"
    assert "绝对值" in v.hypotheses[0].observed


def test_verify_marks_supported_when_direction_holds():
    from fieldmate.exp.verify import verify
    rows = [{"solver": "a", "drift": -0.01}, {"solver": "b", "drift": 0.20}]
    v = verify(_tiny_prereg(), rows, {})
    assert v.hypotheses[0].verdict == "SUPPORTED"


def test_verify_downgrades_to_inconclusive_when_resolution_dominates():
    """误差落在 res_floor 量级内 -> 不能断言方法更差。"""
    from fieldmate.exp.verify import verify
    d = _tiny_prereg()
    d["hypotheses"][0]["metric"] = "err"
    d["hypotheses"][0]["statement"] = "a 的误差小于 b"
    d["hypotheses"][0]["support_required"] = ["res_floor"]
    rows = [{"solver": "a", "err": 0.020}, {"solver": "b", "err": 0.030}]
    v = verify(d, rows, {"res_floor": 0.027})
    assert v.hypotheses[0].verdict == "INCONCLUSIVE", v.to_dict()


def test_verify_detects_backfilled_preregistration():
    from fieldmate.exp.verify import verify
    rows = [{"solver": "a", "drift": 0.01}, {"solver": "b", "drift": 0.02}]
    v = verify(_tiny_prereg(created="2026-06-01T00:00:00Z"), rows,
               {"created": "2026-05-01T00:00:00Z"})
    assert v.backfilled is True
    assert any("事后补注册" in n for n in v.notes)


def test_verify_untested_when_metric_absent():
    """缺结果不是否定证据 —— 必须判 UNTESTED 而不是 REFUTED。"""
    from fieldmate.exp.verify import verify
    rows = [{"solver": "a", "other": 1.0}, {"solver": "b", "other": 2.0}]
    v = verify(_tiny_prereg(), rows, {})
    assert v.hypotheses[0].verdict == "UNTESTED"
    import re as _re
    from fieldmate.compare.matrix import REPORT_ITEMS
    for name, pat, did in REPORT_ITEMS:
        _re.compile(pat, _re.IGNORECASE)          # 编译失败会直接抛
        assert did.startswith("D-"), f"{name} 关联缺陷 id 异常：{did}"


def test_goldset_labels_are_binary_and_have_evidence():
    from fieldmate.eval.prf import load_goldset
    for g in load_goldset():
        assert g["label"] in (0, 1), g
        assert g.get("evidence", "").strip(), f"{g} 缺 evidence 理由"


def test_report_item_regexes_reject_contextual_false_positives():
    """回归测试：这些词在真实论文里绝大多数不是我们要的意思。

    收紧前的实测精确率：报稳定条件 0.00 / 报分辨率 0.25 / 报时间步 0.25。
    这里把最典型的假阳性句固定为负例，防止有人把正则改回宽匹配。
    """
    from fieldmate.compare.matrix import re_search, REPORT_ITEMS
    import re as _re
    pats = {n: _re.compile(p, _re.IGNORECASE) for n, p, _ in REPORT_ITEMS}

    # 物理稳定性 / 训练稳定性 / rollout 稳定性 ≠ 数值稳定性
    for bad in ("The local free energy term f determines the stability of the phase",
                "further improves training stability and generative performance",
                "As the manganese concentration increases, austenite becomes more stable",
                "establishes the value of stochastic re-sampling for long-horizon stability"):
        assert not pats["报稳定条件"].search(bad), f"报稳定条件 假阳性：{bad[:50]}"

    # 显微分辨率 / 特征图分辨率 / 数据分辨率 ≠ 数值网格分辨率
    for bad in ("high-resolution optical micrographs",
                "the spatial resolution of the feature map before the residual blocks",
                "allows them to be trained on low-resolution data"):
        assert not pats["报分辨率"].search(bad), f"报分辨率 假阳性：{bad[:50]}"

    # 序列步 ≠ 格式时间步
    for bad in ("the predicted and ground-truth states at time step k for trajectory j",
                "measured at 101 time steps along the 128x128 grid"):
        assert not pats["报时间步"].search(bad), f"报时间步 假阳性：{bad[:50]}"

    # 真阳性必须仍然命中
    assert pats["报稳定条件"].search("the scheme is unconditionally stable")
    assert pats["报分辨率"].search("yielding an element size h = 0.0025 mm")
    assert pats["报时间步"].search("Let tau > 0 be a time step size")
    assert pats["保体积/守恒"].search("conserved order parameters")
    assert re_search.__module__          # 保持 import 有被使用


# ------------------------------------------------------------ 精进点的族标签
# 实测代价：247 篇语料上「开源自复现」出现 5 次、分母 68/25/123/15/12，
# 而输出里**没有任何字段**说明这 5 条分别是在哪个子群里算的。
# 读者既不知道该信哪条，也无法回去核对任何一条 —— 而这恰恰是一个
# 「专门揭露未报告」的工具最不能出的错。


class _Row:
    """`mine_gaps` 依赖的行对象桩。

    字段必须跟 `MatrixRow` 对齐 —— 早先这里只给了 family/reported/has_fulltext，
    加上「出处名单」功能后 mine_gaps 要读 paper_id/year/title/venue，
    桩对象缺字段就直接 AttributeError。测试替身与真实结构脱节，
    改生产代码时它反而会给出误导性的通过/失败。
    """

    def __init__(self, family, reported, has_fulltext=True, pid=None):
        self.family = family
        self.reported = reported
        self.has_fulltext = has_fulltext
        self.paper_id = pid or f"{family}-{id(self) % 10000:04d}"
        self.year = 2026
        self.title = f"paper in {family}"
        self.venue = "arxiv"
        self.defects = []
        self.undecidable = []


def test_gap_carries_the_family_its_rate_was_computed_over():
    from fieldmate.gaps.mine import mine_gaps
    rows = []
    # 族 A：8 篇全缺「报分辨率」；族 B：4 篇（低于 min_n，应被排除）
    rows += [_Row("A", {"报分辨率": False}) for _ in range(8)]
    rows += [_Row("B", {"报分辨率": False}) for _ in range(4)]
    gaps = mine_gaps(rows, {}, min_n=5, min_rate=0.5)
    assert len(gaps) == 1
    assert gaps[0].family == "A", "缺失率必须带上它所属的族"
    assert gaps[0].total == 8


def test_gap_to_dict_exposes_family():
    from fieldmate.gaps.mine import mine_gaps
    rows = [_Row("时间分数阶", {"报时间步": False}) for _ in range(6)]
    g = mine_gaps(rows, {}, min_n=5, min_rate=0.5)[0]
    assert g.to_dict()["family"] == "时间分数阶"


def test_gaps_json_reports_distinct_item_count_and_families():
    """候选条数是「族 × 条目」组合数；不报去重条目数会被误读成机会数。"""
    from fieldmate.gaps.mine import gaps_json, mine_gaps
    rows = []
    for fam in ("A", "B"):
        rows += [_Row(fam, {"报分辨率": False, "报时间步": False}) for _ in range(6)]
    gs = mine_gaps(rows, {}, min_n=5, min_rate=0.5)
    out = json.loads(gaps_json(gs, n_papers=24))
    assert out["n_candidates"] == 4          # 2 族 × 2 条目
    assert out["n_distinct_items"] == 2      # 但机会只有 2 个
    assert out["families"] == ["A", "B"]


def test_gaps_markdown_does_not_confuse_candidate_count_with_family_count():
    """22 条候选 ≠ 22 个族。早先版本两处都写了 len(gaps)，
    于是「6 个条目 × 22 个族」这种自相矛盾的话印在报告第一行。"""
    from fieldmate.gaps.mine import gaps_markdown, mine_gaps
    rows = []
    for fam in ("A", "B"):
        rows += [_Row(fam, {"报分辨率": False, "报时间步": False}) for _ in range(6)]
    md = gaps_markdown(mine_gaps(rows, {}, min_n=5, min_rate=0.5), n_papers=24)
    assert "候选 4 条 = 2 个条目 × 2 个族" in md
    # 族标签必须出现在每条标题上
    assert "〔族：A〕" in md and "〔族：B〕" in md
