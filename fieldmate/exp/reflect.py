"""reflect.py — 科学损失梯度反向传播与假说自愈反思引擎 (借鉴 Stanford TextGrad / AutoSaddler)

当实验核验被推翻 (REFUTED) 或因混淆因素不可判定 (INCONCLUSIVE) 时：
1. 提取实测多指标误差特征向量（radius_drift, chamfer, normal_err, res_floor）；
2. 映射至相场与几何物理缺陷库 (D-MOD-001, D-EVA-002, D-RES-001 等)；
3. 输出可解释的物理根因诊断与具备自愈能力的新一轮实验参数补丁 (Patches)。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .verify import verify_file

__all__ = ["ReflectionDiagnosis", "reflect_on_results", "reflection_markdown"]


@dataclass
class FailureGradient:
    hypothesis_id: str
    metric: str
    verdict: str  # REFUTED | INCONCLUSIVE
    observed_diff: str
    physical_root_cause: str
    related_defects: list[str]
    suggested_solver: str
    suggested_param_patches: dict[str, Any]
    refined_hypothesis: dict[str, Any]


@dataclass
class ReflectionDiagnosis:
    prereg_id: str
    total_hypotheses: int
    refuted_count: int
    inconclusive_count: int
    gradients: list[FailureGradient] = field(default_factory=list)
    actionable_summary: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def reflect_on_results(
    prereg_path: str | Path,
    results_path: str | Path,
) -> ReflectionDiagnosis:
    """对核验失败/混淆的实验结果反向计算科学损失梯度并输出自愈建议。"""
    verdict = verify_file(prereg_path, results_path)
    results_dict = json.loads(Path(results_path).read_text(encoding="utf-8"))

    res_floor = results_dict.get("res_floor") if isinstance(results_dict, dict) else None

    gradients: list[FailureGradient] = []
    actions: list[str] = []

    for hv in verdict.hypotheses:
        if hv.verdict not in ("REFUTED", "INCONCLUSIVE"):
            continue

        metric = hv.metric
        # 默认回退物理归因
        cause = "实验实测与预注册假说存在显著偏离，需根据物理与离散机制针对性调整。"
        defects = []
        solver_sug = ""
        param_patches: dict[str, Any] = {}
        refined_hypo: dict[str, Any] = {}

        if metric == "radius_drift":
            cause = (
                "纯 Allen-Cahn 驱动的是平均曲率流，闭合曲面在毛细压力驱动下必然自发收缩腐蚀 "
                "(D-MOD-001)，导致几何体积与半径严重失真。"
            )
            defects = ["D-MOD-001", "D-RES-001"]
            solver_sug = "pf_ac_conserved"
            param_patches = {
                "solver": "pf_ac_conserved",
                "lam": 1.2,
                "note": "启用拉格朗日体积乘子守恒，并将保真项系数提升",
            }
            refined_hypo = {
                "id": hv.id,
                "statement": "|radius_drift(pf_ac_conserved)| < 0.02",
                "metric": "radius_drift",
                "expected": "pf_smaller",
                "falsification": "若实测 |radius_drift(pf_ac_conserved)| >= 0.02，则该假说被推翻",
            }
            actions.append(
                f"[{hv.id} 体积漂移] 切换至 pf_ac_conserved 求解器，引入非局部拉格朗日乘子。"
            )

        elif metric in ("chamfer", "p2m"):
            if "none" in hv.statement or "identity" in hv.statement:
                cause = (
                    "去噪结果劣于无操作恒等基线 (none)，表明相场或滤波扩散过度，"
                    "将真实几何特征抹平 (D-EVA-002)。"
                )
                defects = ["D-EVA-002", "D-RES-001"]
                solver_sug = "pf_ac_convex"
                param_patches = {
                    "iters_mult": 0.5,
                    "dt_mult": 0.8,
                    "note": "缩减总扩散时间 t = iters * dt，防止过度平滑",
                }
            else:
                cause = "几何精度未达到预期基线，受离散网格量化或迭代收敛不足影响。"
                defects = ["D-EVA-001"]
                solver_sug = "pf_ac_convex"
                param_patches = {"nn_mult": 1.8, "note": "适度细化网格步长 h"}
            actions.append(
                f"[{hv.id} 几何误差] 抑制过度扩散，建议将迭代轮数缩减 50% 或减小时间步长。"
            )

        elif metric in ("normal_err", "hausdorff"):
            cause = (
                "法向或最大几何误差恶化，各向同性拉普拉斯算子模糊了尖锐法向角点与高曲率棱边 "
                "(D-FEA-001)。"
            )
            defects = ["D-FEA-001"]
            solver_sug = "pf_ac_aniso"
            param_patches = {
                "solver": "pf_ac_aniso",
                "k_tensor": 0.5,
                "note": "引入法向各向异性扩散张量保护几何边缘",
            }
            actions.append(
                f"[{hv.id} 特征退化] 切换至 pf_ac_aniso 求解器，沿切向平滑、法向抑制扩散。"
            )

        if "res_floor" in hv.confound or (res_floor and "小于分辨率噪声" in hv.confound):
            cause += (
                f" 当前差异受空间分辨率下限 res_floor={res_floor:.4g} 支配，"
                f"无法区分方法优劣 (D-EVA-001)。"
            )
            defects.append("D-EVA-001")
            param_patches["n_vox_min"] = 64
            actions.append(
                f"[{hv.id} 分辨率受限] 将网格密度调大（n_vox >= 64），"
                f"使真实效应量显著大于 res_floor。"
            )

        gradients.append(
            FailureGradient(
                hypothesis_id=hv.id,
                metric=metric,
                verdict=hv.verdict,
                observed_diff=hv.observed,
                physical_root_cause=cause,
                related_defects=sorted(list(set(defects))),
                suggested_solver=solver_sug or "pf_ac_convex",
                suggested_param_patches=param_patches,
                refined_hypothesis=refined_hypo,
            )
        )

    return ReflectionDiagnosis(
        prereg_id=verdict.prereg_id,
        total_hypotheses=len(verdict.hypotheses),
        refuted_count=len(verdict.refuted),
        inconclusive_count=len(verdict.inconclusive),
        gradients=gradients,
        actionable_summary=actions,
    )


def reflection_markdown(diag: ReflectionDiagnosis) -> str:
    """渲染自愈反思梯度报告。"""
    lines = [
        "# 🔁 实验负结果自愈与反向反思报告 (Scientific Reflection)",
        f"- **目标预注册**：`{diag.prereg_id}`",
        f"- **推翻假说**：{diag.refuted_count} 项 | **未决/混淆**：{diag.inconclusive_count} 项",
        "",
        "---",
        "",
        "## 一、 失败梯度物理溯源与病因切片",
    ]

    for g in diag.gradients:
        lines.extend([
            f"### 假说 `{g.hypothesis_id}` ({g.verdict}) — 指标: `{g.metric}`",
            f"- **观测结果**：{g.observed_diff}",
            f"- **物理根因**：{g.physical_root_cause}",
            f"- **关联缺陷**：{', '.join(g.related_defects) if g.related_defects else '无'}",
            f"- **建议自愈求解器**：`{g.suggested_solver}`",
            f"- **建议参数补丁**：`{json.dumps(g.suggested_param_patches, ensure_ascii=False)}`",
            "",
        ])

    if diag.actionable_summary:
        lines.extend([
            "---",
            "",
            "## 二、 下一轮自主迭代可执行建议 (Next Iteration Action)",
        ])
        for act in diag.actionable_summary:
            lines.append(f"- [ ] {act}")
        lines.append("")

    return "\n".join(lines)
