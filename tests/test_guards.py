r"""test_guards.py — 判定引擎修复的回归测试（2026-10）

守住四类真实缺陷：
  1. exists_counterexample 落进 _cmp 恒 False —— 不看数据必判 REFUTED
  2. 缺必要对照（res_floor / identity_baseline / noise_model）照样拿 SUPPORTED
  3. _check_controls 对未知对照项裸下标 KeyError
  4. _classify 小样本守卫被全文率短路
"""



def _prereg(expected="pf_smaller", support=None, metric="drift"):
    return {
        "id": "t-guard", "created": "2026-01-01T00:00:00Z",
        "claim": "测试主张",
        "hypotheses": [{
            "id": "H1", "statement": "a 的 |drift| 小于 b", "metric": metric,
            "compare": "a vs b", "expected": expected,
            "falsification": "若不成立，H1 被推翻",
            "support_required": support or []}],
        "confounds": ["分辨率"],
    }


# ---------------------------------------------------------------- exists_counterexample
def test_exists_counterexample_without_list_is_untested():
    """缺反例清单不是否定证据 —— 必须 UNTESTED 并告知如何提供。"""
    from fieldmate.exp.verify import verify
    rows = [{"solver": "a", "m": 1.0}, {"solver": "b", "m": 2.0}]
    v = verify(_prereg(expected="exists_counterexample", metric="m"), rows, {})
    assert v.hypotheses[0].verdict == "UNTESTED"
    assert "counterexamples" in v.hypotheses[0].reason


def test_exists_counterexample_with_list_is_supported():
    from fieldmate.exp.verify import verify
    rows = [{"solver": "a", "m": 1.0}]
    meta = {"counterexamples": [{"solver": "x", "metric": "m", "value": 3.0,
                                 "note": "在某设置下违反主张"}]}
    v = verify(_prereg(expected="exists_counterexample", metric="m"), rows, meta)
    assert v.hypotheses[0].verdict == "SUPPORTED"
    assert "x" in v.hypotheses[0].observed


# ---------------------------------------------------------------- 必要对照闸门
def test_missing_res_floor_is_inconclusive_not_supported():
    """回归：跳过 prereg --results 直接核验时，缺 res_floor 照样 SUPPORTED。"""
    from fieldmate.exp.verify import verify
    d = _prereg(support=["res_floor"])
    rows = [{"solver": "a", "drift": -0.01}, {"solver": "b", "drift": 0.20}]
    v = verify(d, rows, {})                       # 没给 res_floor
    assert v.hypotheses[0].verdict == "INCONCLUSIVE"


def test_missing_identity_baseline_is_inconclusive():
    from fieldmate.exp.verify import verify
    d = _prereg(support=["identity_baseline"])
    rows = [{"solver": "a", "drift": -0.01}, {"solver": "b", "drift": 0.20}]
    v = verify(d, rows, {})                       # 没有 none/identity 行
    assert v.hypotheses[0].verdict == "INCONCLUSIVE"


def test_identity_baseline_present_supports():
    from fieldmate.exp.verify import verify
    d = _prereg(support=["identity_baseline"])
    rows = [{"solver": "a", "drift": -0.01}, {"solver": "b", "drift": 0.20},
            {"solver": "none", "drift": 0.0}]
    v = verify(d, rows, {})
    assert v.hypotheses[0].verdict == "SUPPORTED"


def test_res_floor_zero_declared_does_not_block():
    """显式声明 res_floor=0.0（无量化下限）算对照在场、只是不阻断。"""
    from fieldmate.exp.verify import verify
    d = _prereg(support=["res_floor"])
    rows = [{"solver": "a", "drift": -0.01}, {"solver": "b", "drift": 0.20}]
    v = verify(d, rows, {"res_floor": 0.0})
    assert v.hypotheses[0].verdict == "SUPPORTED"


# ---------------------------------------------------------------- KeyError 修复
def test_validate_with_results_unknown_control_no_keyerror():
    """回归：CONFOUND_GUARDS[req] 裸下标 —— 未知对照项 validate 直接崩溃。"""
    from fieldmate.exp.prereg import validate
    d = _prereg(support=["bogus_control"])
    results = {"rows": [{"solver": "a", "drift": 1.0}], "res_floor": 0.1}
    problems = validate(d, results)               # 不应抛 KeyError
    assert any("bogus_control" in x for x in problems)


# ---------------------------------------------------------------- _classify 修复
def test_classify_small_sample_weak_even_with_full_fulltext():
    """回归：fulltext_ratio>=0.8 排在 n<5 之前，小样本高全文率被判 STRONG。"""
    from fieldmate.gaps.mine import _classify
    assert _classify(1.0, "low", 1.0, 3) == "WEAK"
    assert _classify(1.0, "medium", 0.9, 4) == "WEAK"
    assert _classify(1.0, "medium", 0.9, 8) == "STRONG"   # 样本够 + 全文支撑
    assert _classify(0.5, "medium", 0.5, 8) == "MEDIUM"


# ---------------------------------------------------------------- 时间戳解析
def test_backfill_detection_tolerates_timezone_suffix():
    """回归：裸字符串比较会把带 +00:00 的同刻时间误判为事后补注册。"""
    from fieldmate.exp.verify import verify
    rows = [{"solver": "a", "drift": 0.01}, {"solver": "b", "drift": 0.02}]
    v = verify(_prereg(), rows,
               {"created": "2026-01-01T00:00:00+00:00"})   # 与预注册同刻
    assert v.backfilled is False
