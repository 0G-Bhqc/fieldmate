# ------------------------------------------------ Phase 3 判断层（--llm-cmd）
# 假 LLM 命令：覆盖协议全路径（成功 / 任务被拒 / 坏 JSON / 非零退出 / 超时），
# 以及两条真实任务的固定回答。行为经 FAKE_MODE 环境变量切换。
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

FAKE_LLM_TEMPLATE = r'''
import json, sys
mode = "@MODE@"
if mode == "exit1":
    sys.exit(3)
if mode == "badjson":
    print("not json at all")
    raise SystemExit(0)
if mode == "nook":
    print(json.dumps({"result": {}}))
    raise SystemExit(0)
req = json.load(sys.stdin)
task = req.get("task")
if task == "assumption_refine":
    sents = req["data"]["sentences"]
    cands = []
    if sents:
        cands.append({"quote": sents[0], "kind": "model",
                      "rationale": "把材料均匀性当成立", "confidence": "high"})
        cands.append({"quote": sents[0], "kind": "bogus-kind",
                      "rationale": "kind 非法", "confidence": "high"})
    cands.append({"quote": "This sentence is hallucinated and never in the input.",
                  "kind": "scope", "rationale": "x", "confidence": "low"})
    print(json.dumps({"ok": True, "result": {"candidates": cands}}))
elif task == "subdomain_filter":
    if "RELEVANT" in req["data"]["title"]:
        print(json.dumps({"ok": True,
                          "result": {"relevant": True, "reason": "方法与对象一致"}}))
    else:
        print(json.dumps({"ok": True,
                          "result": {"relevant": False, "reason": "词面沾边"}}))
elif task == "assumption_scan":
    chunks = req["data"]["chunks"]
    cands = []
    if chunks:
        body = chunks[0]
        i = body.find(" the ")
        quote = body[max(0, i - 10): i + 80].strip()
        if quote:
            cands.append({"quote": quote, "kind": "numerical",
                          "rationale": "参数取值未论证", "confidence": "medium"})
    cands.append({"quote": "Hallucinated implicit assumption not in any chunk text.",
                  "kind": "model", "rationale": "x", "confidence": "high"})
    print(json.dumps({"ok": True, "result": {"candidates": cands}}))
else:
    print(json.dumps({"ok": False, "error": "unknown task"}))
'''


@pytest.fixture()
def fake_llm_cmd(tmp_path):
    script = tmp_path / "fake_llm.py"
    script.write_text(FAKE_LLM_TEMPLATE.replace("@MODE@", "ok"), encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


def _spawn(mode: str, tmp_path: Path) -> str:
    script = tmp_path / "fake_llm.py"
    script.write_text(FAKE_LLM_TEMPLATE.replace("@MODE@", mode), encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


# ------------------------------------------------ run_with_cmd 协议
def test_run_with_cmd_returns_result(fake_llm_cmd):
    from fieldmate.llm import run_with_cmd
    out = run_with_cmd(fake_llm_cmd, {"task": "assumption_refine", "data": {"sentences": []}})
    assert isinstance(out, dict)


def test_run_with_cmd_task_rejected(tmp_path):
    from fieldmate.llm import LLMError, run_with_cmd
    with pytest.raises(LLMError, match="unknown task"):
        run_with_cmd(_spawn("ok", tmp_path), {"task": "no-such-task"})


def test_run_with_cmd_bad_json(tmp_path):
    from fieldmate.llm import LLMError, run_with_cmd
    with pytest.raises(LLMError, match="不是合法 JSON"):
        run_with_cmd(_spawn("badjson", tmp_path), {"task": "x"})


def test_run_with_cmd_protocol_violation(tmp_path):
    from fieldmate.llm import LLMError, run_with_cmd
    with pytest.raises(LLMError, match="缺 ok"):
        run_with_cmd(_spawn("nook", tmp_path), {"task": "x"})


def test_run_with_cmd_nonzero_exit(tmp_path):
    from fieldmate.llm import LLMError, run_with_cmd
    with pytest.raises(LLMError, match="退出码 3"):
        run_with_cmd(_spawn("exit1", tmp_path), {"task": "x"})


def test_run_with_cmd_timeout(tmp_path):
    from fieldmate.llm import LLMError, run_with_cmd
    slow = f'"{sys.executable}" -c "import time; time.sleep(2)"'
    with pytest.raises(LLMError, match="超时"):
        run_with_cmd(slow, {"task": "x"}, timeout=0.3)


# ------------------------------------------------ 预滤器与幻觉闸门
def test_assumption_prefilter_broad_recall():
    """预滤器必须比槽位正则召回更宽：槽位正则在真实论文上 Assumption 0 命中。"""
    from fieldmate.extract.refine import assumption_prefilter
    text = ("We assume the material is homogeneous throughout the domain. "
            "The interface is tracked by the level set function. "
            "It is assumed that the noise is independent and identically distributed. "
            "Numerical experiments confirm the convergence rate.")
    sents = assumption_prefilter(text)
    assert len(sents) == 2, sents
    assert any("homogeneous" in s for s in sents)
    assert all("level set" not in s for s in sents)   # 无标记句不进预滤


def test_validate_candidates_hallucination_gate():
    """判定归脚本：quote 必须逐字命中输入句；幻觉/坏枚举/空理由全部丢弃并记账。"""
    from fieldmate.extract.refine import validate_candidates
    sents = ["We assume the material is homogeneous in the computed domain."]
    result = {"candidates": [
        {"quote": sents[0], "kind": "model", "rationale": "未验证前提", "confidence": "high"},
        {"quote": "Fabricated sentence that appears nowhere in the input text.",
         "kind": "scope", "rationale": "x", "confidence": "low"},
        {"quote": sents[0], "kind": "bogus", "rationale": "y", "confidence": "high"},
        {"quote": sents[0], "kind": "model", "rationale": "  ", "confidence": "high"},
        {"quote": "short", "kind": "model", "rationale": "z", "confidence": "high"},
    ]}
    kept, dropped = validate_candidates(result, sents)
    assert len(kept) == 1 and kept[0]["confirmed"] is False
    assert len(dropped) == 4
    assert any("幻觉" in d for d in dropped)
    assert all("bogus" not in json.dumps(k) for k in kept)


def test_validate_candidates_non_dict_result():
    from fieldmate.extract.refine import validate_candidates
    kept, dropped = validate_candidates("not a dict", ["x" * 40])
    assert kept == [] and dropped and "不是对象" in dropped[0]


# ------------------------------------------------ 端到端（假 LLM）
def test_refine_assumptions_end_to_end(fake_llm_cmd):
    from fieldmate.extract.refine import refine_assumptions
    text = ("We assume the material is homogeneous in the computed domain of size L. "
            "The interface is tracked separately.")
    out = refine_assumptions(text, fake_llm_cmd)
    assert out["n_prefiltered"] == 1
    assert len(out["candidates"]) == 1                     # 幻觉 + 坏 kind 被闸门剔除
    assert "homogeneous" in out["candidates"][0]["quote"]
    assert len(out["dropped"]) == 2


def test_refine_assumptions_no_markers(fake_llm_cmd):
    from fieldmate.extract.refine import refine_assumptions
    out = refine_assumptions("The method converges quadratically under mild conditions here.",
                             fake_llm_cmd)
    assert out["mode"] == "chunk-scan"


def test_subdomain_check_both_branches(fake_llm_cmd, monkeypatch):
    from fieldmate.extract.refine import subdomain_check
    monkeypatch.setenv("FAKE_MODE", "ok")
    ok, why = subdomain_check(fake_llm_cmd, "q", "title with RELEVANT marker", "abs")
    assert ok is True and why
    ok2, why2 = subdomain_check(fake_llm_cmd, "q", "plain title", "abs")
    assert ok2 is False and why2 == "词面沾边"


def test_subdomain_check_invalid_relevant_type(tmp_path, monkeypatch):
    from fieldmate.extract.refine import subdomain_check
    from fieldmate.llm import LLMError
    script = tmp_path / "bad.py"
    script.write_text('import json,sys\n'
                      'print(json.dumps({"ok": True, "result": {"relevant": "yes"}}))',
                      encoding="utf-8")
    monkeypatch.delenv("FAKE_MODE", raising=False)
    with pytest.raises(LLMError, match="布尔"):
        subdomain_check(f'"{sys.executable}" "{script}"', "q", "t", "a")


# ------------------------------------------------ harvest 闸门 C
def test_harvest_llm_gate_moves_irrelevant_to_rejection_log(tmp_path, monkeypatch):
    """闸门 C：LLM 判 irrelevant 的论文不进语料，理由进 rejection log 可复核。"""
    from fieldmate.sources import corpus as corpus_mod
    from fieldmate.sources.arxiv import Paper

    papers = [Paper(id="a1", title="RELEVANT: phase-field surface reconstruction",
                    abstract="phase-field method for reconstruction",
                    categories=["math.NA"]),
              Paper(id="b2", title="plain: training dynamics of small networks",
                    abstract="We revisit the Allen-Cahn equation as a convenient "
                             "test problem for studying optimizer dynamics.",
                    categories=["cs.LG", "math.NA"])]
    monkeypatch.setattr(corpus_mod, "collect", lambda *a, **k: papers)
    qs = tmp_path / "queries.json"
    qs.write_text(json.dumps({"queries": [{"query": "abs:phase-field",
                                           "label": "测试线", "purity": "high"}]},
                             ensure_ascii=False),
                  encoding="utf-8")
    m = corpus_mod.harvest(out=tmp_path / "corpus.json", per_query=10,
                           do_download=False, queries_path=qs, verbose=False,
                           llm_cmd=_spawn("ok", tmp_path))
    r = m["per_query"][0]
    assert [p["id"] for p in r["accepted"]] == ["a1"]
    rej = [x for x in r["rejected"] if x["id"] == "b2"]
    assert len(rej) == 1 and "[LLM 子领域闸门]" in rej[0]["reason"]


def test_read_refine_requires_llm_cmd():
    """H6：--refine-assumptions 不给 --llm-cmd 必须显式失败（退出码 3），不静默跳过。"""
    from fieldmate.cli import EXIT_ARGS, main
    assert main(["read", "--refine-assumptions"]) == EXIT_ARGS


def test_refine_assumptions_chunk_scan_for_marker_free_text(fake_llm_cmd):
    """真实期刊论文几乎零假设类措辞（P1 全文只 1 处 simplif）——
    无标记文本必须走 chunk-scan 找隐式假设，幻觉闸门照常生效。"""
    from fieldmate.extract.refine import refine_assumptions
    text = ("The algorithm iterates over all triangles of the surface mesh. "
            "The parameter lambda is set to 0.5 and the time step is fixed. "
            "Convergence is observed on the finest grid of the sequence.")
    out = refine_assumptions(text, fake_llm_cmd)
    assert out["mode"] == "chunk-scan" and out["n_chunks"] >= 1
    assert len(out["candidates"]) == 1                 # 幻觉候选被闸门剔除
    assert out["candidates"][0]["quote"] in text.replace("\n", " ") or \
        out["candidates"][0]["quote"] in text
    assert any("幻觉" in d for d in out["dropped"])
