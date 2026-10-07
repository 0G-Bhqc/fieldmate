r"""test_extreme.py — fieldmate 判定引擎的极端输入测试

原则：垃圾进 → 要么可读的 UNTESTED，要么显式报错；绝不静默给错判定、
也绝不 traceback 炸穿 CLI。
"""

import json

import pytest


def _prereg(n_hyp=1, expected="pf_smaller", metric="drift"):
    hs = []
    for i in range(n_hyp):
        hs.append({
            "id": f"H{i+1}", "statement": f"a 的 |{metric}| 小于 b（#{i+1}）",
            "metric": metric, "compare": "a vs b", "expected": expected,
            "falsification": "若不成立，H1 被推翻", "support_required": []})
    return {
        "id": "t-extreme", "created": "2026-01-01T00:00:00Z",
        "claim": "测试主张", "hypotheses": hs, "confounds": ["分辨率"],
    }


def _rows(metric="drift", a=-0.01, b=0.20):
    return [{"solver": "a", metric: a}, {"solver": "b", metric: b}]


# ---------------------------------------------------------------- 垃圾指标值
def test_verify_non_numeric_metric_is_untested_not_crash():
    """指标值是字符串/None/嵌套结构：可读 UNTESTED，不许 float() 炸穿。"""
    from fieldmate.exp.verify import verify
    for bad in ("0.05abc", None, [1, 2], {"v": 1}):
        rows = [{"solver": "a", "drift": bad}, {"solver": "b", "drift": 0.1}]
        v = verify(_prereg(), rows, {})
        assert v.hypotheses[0].verdict == "UNTESTED", bad
        assert "不是数值" in v.hypotheses[0].reason


def test_verify_numeric_strings_are_accepted():
    """"0.05" 这种可转数值的字符串应该照常工作（宽容输入）。"""
    from fieldmate.exp.verify import verify
    rows = [{"solver": "a", "drift": "0.05"}, {"solver": "b", "drift": "0.20"}]
    v = verify(_prereg(), rows, {})
    assert v.hypotheses[0].verdict == "SUPPORTED"


# ---------------------------------------------------------------- 结构垃圾
def test_verify_rows_without_solver_key_no_crash():
    from fieldmate.exp.verify import verify
    rows = [{"drift": 1.0}, {"solver": "a", "drift": 0.01},
            {"solver": "b", "drift": 0.20}, None]
    v = verify(_prereg(), rows, {})
    assert v.hypotheses[0].verdict == "SUPPORTED"


def test_verify_compare_with_extra_spaces():
    from fieldmate.exp.verify import verify
    d = _prereg()
    d["hypotheses"][0]["compare"] = "  a   vs   b  "
    v = verify(d, _rows(), {})
    assert v.hypotheses[0].verdict == "SUPPORTED"


def test_verify_scale_200_hypotheses():
    from fieldmate.exp.verify import verify
    v = verify(_prereg(n_hyp=200), _rows(), {})
    assert len(v.hypotheses) == 200
    assert all(h.verdict == "SUPPORTED" for h in v.hypotheses)


def test_exists_counterexample_with_garbage_entries():
    from fieldmate.exp.verify import verify
    meta = {"counterexamples": [{"solver": None}, "junk", {"value": 3}]}
    v = verify(_prereg(expected="exists_counterexample"), _rows(), meta)
    assert v.hypotheses[0].verdict == "SUPPORTED"      # 有清单就算支持，条目形状宽容


# ---------------------------------------------------------------- 前向兼容
def test_prereg_load_ignores_unknown_fields():
    """未来版本字段/手写多写的 key：load 不炸（validate 走 raw dict 本来不炸）。"""
    from fieldmate.exp.prereg import Prereg
    d = _prereg()
    d["hypotheses"][0]["future_field_v2"] = {"whatever": True}
    d["top_level_extra"] = 123
    p = Prereg.from_dict(d)          # 不应 TypeError
    assert p.hypotheses[0].id == "H1"


def test_validate_special_regex_chars_in_claim():
    from fieldmate.exp.prereg import validate
    d = _prereg()
    d["claim"] = "a.*[b+(c)? 更准 ^$ \\ 的主张 ()*+?"
    problems = validate(d)
    assert isinstance(problems, list)


def test_validate_scale_500_hypotheses():
    from fieldmate.exp.prereg import validate
    problems = validate(_prereg(n_hyp=500))
    assert problems == [] or isinstance(problems, list)


def test_verify_file_malformed_json_fails_loud():
    """坏 JSON 必须显式抛错（静默吞掉才是灾难）。"""
    import tempfile
    import os
    from fieldmate.exp.verify import verify_file
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                      encoding="utf-8")
    tmp.write("{ not valid json !!")
    tmp.close()
    try:
        with pytest.raises(json.JSONDecodeError):
            verify_file(tmp.name, tmp.name)
    finally:
        os.unlink(tmp.name)


# ---------------------------------------------------------------- 时钟边界
def test_backfill_mixed_timezone_formats():
    from fieldmate.exp.verify import verify
    rows = _rows(a=0.01, b=0.02)
    # 2025-12-31T20:00+08:00 == 2025-12-31T12:00Z，早于预注册 00:00Z -> 补注册
    v = verify(_prereg(), rows, {"created": "2025-12-31T20:00:00+08:00"})
    assert v.backfilled is True
    # 2026-01-01T16:00+08:00 == 2026-01-01T08:00Z，晚于预注册 -> 合法
    v = verify(_prereg(), rows, {"created": "2026-01-01T16:00:00+08:00"})
    assert v.backfilled is False
