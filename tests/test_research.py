"""test_research.py — 突破点发现与实验设计器自动化单测"""

from __future__ import annotations

import json
import subprocess
import sys

from fieldmate.exp.prereg import validate
from fieldmate.research.breakthrough import (
    BREAKTHROUGH_ARCHETYPES,
    Breakthrough,
    breakthrough_markdown,
    discover_breakthroughs,
)
from fieldmate.research.designer import (
    design_from_breakthrough,
    design_from_idea,
)


def test_discover_breakthroughs_from_synthetic_text():
    sample_text = """
    We propose a phase field surface reconstruction algorithm based on the Allen-Cahn equation.
    The governing equation is discretized using a forward Euler explicit scheme with time-step limit
    controlled by the CFL condition: dt = 0.05 * h^2.
    However, the isotropic laplacian tends to smooth out sharp edges and corners.
    Furthermore, without volume preservation, the mean curvature flow
    causes severe volume loss and shrinkage.
    """
    bts = discover_breakthroughs(sample_text, paper_id="test_p1")
    assert len(bts) >= 3

    ids = {b.id for b in bts}
    assert "BT-STABILITY-CONVEX" in ids
    assert "BT-VOLUME-CONSERVED" in ids
    assert "BT-FEATURE-ANISOTROPIC" in ids

    # 检查 Markdown 渲染不抛错且含关键字段
    md = breakthrough_markdown(bts, paper_desc="Test Paper")
    assert "论文突破点挖掘与研究进阶分析报告" in md
    assert "BT-STABILITY-CONVEX" in md


def test_design_from_idea_stability_convex():
    idea = "我想用凸分裂半隐式能量稳定方法，解决显式相场时间步长太小易发散的问题"
    prereg = design_from_idea(idea)

    d = prereg.to_dict()
    # 核心：必须 100% 通过预注册验证器的机器质检
    problems = validate(d)
    assert not problems, f"预注册验证失败：{problems}"

    assert prereg.backend == "pfdenoise"
    assert len(prereg.hypotheses) >= 2
    h1 = prereg.hypotheses[0]
    assert h1.metric == "radius_drift"
    assert "pf_ac_convex" in h1.compare
    assert any(tok in h1.falsification for tok in ["被推翻", "不成立", "视为失败"])


def test_design_from_idea_volume_preservation():
    idea = "我想引入拉格朗日乘子全局约束，解决传统曲率流相场导致的体积收缩腐蚀"
    prereg = design_from_idea(idea)

    d = prereg.to_dict()
    problems = validate(d)
    assert not problems, f"预注册验证失败：{problems}"

    h1 = prereg.hypotheses[0]
    assert h1.abs is True
    assert "pf_ac_conserved" in h1.compare
    assert "|radius_drift" in h1.statement


def test_design_from_idea_anisotropic_features():
    idea = "我想构建法向各向异性扩散张量，保护点云重建中的尖锐棱边与法向角点"
    prereg = design_from_idea(idea)

    d = prereg.to_dict()
    problems = validate(d)
    assert not problems, f"预注册验证失败：{problems}"

    # 应该包含 normal_error 指标的比较
    metrics = [h.metric for h in prereg.hypotheses]
    assert "normal_error" in metrics


def test_design_from_breakthrough_direct():
    arch = BREAKTHROUGH_ARCHETYPES[0]
    bt = Breakthrough(
        id=arch["id"],
        title=arch["title"],
        category=arch["category"],
        target_bottleneck=arch["target_bottleneck"],
        theoretical_rationale=arch["theoretical_rationale"],
        suggested_method=arch["suggested_method"],
        falsifiable_claim=arch["falsifiable_claim"],
        minimal_experiment=arch["minimal_experiment"],
        confidence="HIGH",
    )
    prereg = design_from_breakthrough(bt, exp_id="exp-test-bt-01")
    assert prereg.id == "exp-test-bt-01"
    problems = validate(prereg.to_dict())
    assert not problems, f"验证失败：{problems}"


def test_cli_design_subcommand(tmp_path):
    out_file = tmp_path / "prereg_cli_test.json"
    cmd = [
        sys.executable,
        "-m",
        "fieldmate",
        "design",
        "--idea",
        "测试半隐式凸分裂解决CFL时间步受限",
        "--format",
        "json",
        "--out",
        str(out_file),
    ]
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert r.returncode == 0, f"CLI 运行失败：{r.stderr}"

    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["status"] == "registered"
    assert len(data["hypotheses"]) >= 2


def test_cli_breakthrough_subcommand_requires_path():
    cmd = [sys.executable, "-m", "fieldmate", "breakthrough"]
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert r.returncode in (2, 3)
    assert "--path" in r.stderr or "--path" in r.stdout
