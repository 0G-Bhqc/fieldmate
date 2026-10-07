"""breakthrough.py — 逆向瓶颈挖掘与学术突破点发现引擎

核心立场（§1 原则）：
1. 判定归脚本：从旧论文正文中基于确定性句法和关键词特征识别数值/物理瓶颈，不凭空脑补。
2. 突破可证伪：每个挖掘出的突破点都必须附带最小实验方案与可被推翻的主张。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = ["Breakthrough", "discover_breakthroughs", "breakthrough_markdown"]


@dataclass
class Breakthrough:
    id: str
    title: str
    category: str  # stability | volume | feature | discretization | noise
    target_bottleneck: str  # 旧论文暴露的物理/数值/实验局限
    theoretical_rationale: str  # 数学与物理机制上的突破原理
    suggested_method: str  # 建议的新技术方案（如对应求解器）
    evidence_quotes: list[str] = field(default_factory=list)  # 旧论文证据句
    confidence: str = "MEDIUM"  # HIGH | MEDIUM | LOW
    falsifiable_claim: str = ""  # 拟提出的核心可证伪主张
    minimal_experiment: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------
# 领域经典瓶颈与突破点知识原型（Archetypes）
# --------------------------------------------------------------------------
BREAKTHROUGH_ARCHETYPES = [
    {
        "id": "BT-STABILITY-CONVEX",
        "category": "stability",
        "title": "半隐式凸分裂无条件能量稳定格式 (Semi-Implicit Convex Splitting)",
        "detect_patterns": [
            r"\bexplicit\s+(?:scheme|time|euler|discretization)\b",
            r"\bcfl\b|\btime[- ]step\s+limit\b",
            r"\b[Δδ∆]\s*t\s*=\s*(?:0\.\d+|cfl|dt)",
            r"forward\s+euler",
            r"instability\s+when\s+dt",
        ],
        "target_bottleneck": (
            "旧方法采用显式前向欧拉格式，受到极其严苛的 CFL 步长限制 (dt <= O(h^2/eps^2))，"
            "高分辨率下迭代步数激增，易发散失稳。"
        ),
        "theoretical_rationale": (
            "将双阱自由能拆解为凸项（隐式离散保证能量耗散）与凹项（显式离散保持线性方程可解），"
            "在离散层面实现无条件能量稳定，允许采用增大数十倍的时间步长。"
        ),
        "suggested_method": (
            "pf_ac_convex (Eyre 凸分裂半隐式相场) 或 pf_ac_convex_adaptive (自适应时间步)"
        ),
        "falsifiable_claim": (
            "在时间步长放大 10 倍以上时，凸分裂格式仍保持无条件能量稳定收敛，"
            "无数值发散溢出，收敛耗时显著优于显式基线。"
        ),
        "minimal_experiment": {
            "solver_test": "pf_ac_convex",
            "solver_baseline": "pf_ac",
            "compare": "pf_ac_convex vs pf_ac",
            "metric": "radius_drift",
            "expected": "pf_smaller",
            "falsification": "若在大时间步长下 pf_ac_convex 发散或漂移量大于基线，则主张被推翻",
        },
    },
    {
        "id": "BT-VOLUME-CONSERVED",
        "category": "volume",
        "title": "拉格朗日乘子全局体积守恒相场 (Volume-Preserving Allen-Cahn)",
        "detect_patterns": [
            r"allen[- ]cahn",
            r"mean\s+curvature\s+flow",
            r"shrinkage|shrinks|evaporat|volume\s+loss",
            r"capillary\s+pressure",
            r"without\s+volume\s+preserv",
        ],
        "target_bottleneck": (
            "纯 Allen-Cahn 驱动的是平均曲率流，闭合表面在毛细压力驱动下必然自发收缩腐蚀，"
            "导致去噪与重建过程中几何体积严重流失。"
        ),
        "theoretical_rationale": (
            "引入全局非局部拉格朗日乘子 beta(t)，通过正相积分守恒硬约束抵消曲率收缩驱动力，"
            "在保证二阶方程低刚性的同时实现严格体积守恒。"
        ),
        "suggested_method": "pf_ac_conserved (带非局部体积约束项的守恒型 AC 求解器)",
        "falsifiable_claim": (
            "体积守恒相场能将去噪过程中的半径与体积漂移抑制至接近 0，显著优于传统显式 AC。"
        ),
        "minimal_experiment": {
            "solver_test": "pf_ac_conserved",
            "solver_baseline": "pf_ac",
            "compare": "pf_ac_conserved vs pf_ac",
            "metric": "radius_drift",
            "expected": "pf_smaller",
            "abs": True,
            "falsification": (
                "若 |radius_drift(pf_ac_conserved)| >= |radius_drift(pf_ac)|，则假设被推翻"
            ),
        },
    },
    {
        "id": "BT-FEATURE-ANISOTROPIC",
        "category": "feature",
        "title": "法向抑制各向异性扩散张量相场 (Anisotropic Diffusion Tensor Phase Field)",
        "detect_patterns": [
            r"isotropic\s+laplacian",
            r"smooths?\s+out\s+sharp\s+edges?",
            r"loss\s+of\s+features?|blurr\w+\s+edges?",
            r"corner\s+rounding",
            r"over[- ]smooth\w*",
        ],
        "target_bottleneck": (
            "各向同性拉普拉斯算子无差别扩散，在高频几何区域平滑抹平了尖锐棱边与法向角点，"
            "降低法向保真度。"
        ),
        "theoretical_rationale": (
            "构建基于局部曲面法向的投影扩散张量 D = I - (1-gamma) n n^T，"
            "切向强平滑去噪，法向正交方向抑制平滑以锁定尖锐特征。"
        ),
        "suggested_method": "pf_ac_aniso (各向异性张量 AC) 或 pf_ac_barrier (边缘指示函数屏障)",
        "falsifiable_claim": (
            "各向异性相场在有效去噪的同时，法向平均夹角误差 (normal_error) 显著优于各向同性基线。"
        ),
        "minimal_experiment": {
            "solver_test": "pf_ac_aniso",
            "solver_baseline": "pf_ac",
            "compare": "pf_ac_aniso vs pf_ac",
            "metric": "normal_error",
            "expected": "pf_smaller",
            "falsification": "若 normal_error(pf_ac_aniso) >= normal_error(pf_ac)，则假设被推翻",
        },
    },
    {
        "id": "BT-SUBVOXEL-CORRECTION",
        "category": "discretization",
        "title": "等值面亚格点重心修正与超分辨率重建 (Sub-voxel Isosurface Correction)",
        "detect_patterns": [
            r"voxel\s+(?:size|grid|resolution)",
            r"resolution\s+limit|discretization\s+error",
            r"staircas\w+|grid\s+artifacts?",
            r"\bh\s*=\s*[\d.]+",
        ],
        "target_bottleneck": (
            "体素化隐式欧拉网格存在离散量化下限 h/2，正相提取时格心离散化带来系统性阶梯效应与漂移。"
        ),
        "theoretical_rationale": (
            "基于正相体素内部标量场连续权重分布，构建亚格点重心偏移向量修正，"
            "突破笛卡尔网格常数分辨率下限，降低 Chamfer 距离。"
        ),
        "suggested_method": "subvoxel_corrected_extraction (带三维连续权重重心的等值面提取算法)",
        "falsifiable_claim": (
            "在同等粗网格尺度下，亚格点重心修正算法能使表面重建 Chamfer 距离突破网格下限 h/2。"
        ),
        "minimal_experiment": {
            "solver_test": "pf_ac_convex",
            "solver_baseline": "laplacian",
            "compare": "pf_ac_convex vs laplacian",
            "metric": "chamfer",
            "expected": "pf_smaller",
            "falsification": "若 chamfer(pf_ac_convex) >= chamfer(laplacian)，则假设被推翻",
        },
    },
    {
        "id": "BT-NOISE-SELF-SUPERVISED",
        "category": "noise",
        "title": "面向有偏/重尾噪声的自监督非配对评测协议 (Self-Supervised Prior-Aware Benchmark)",
        "detect_patterns": [
            r"synthetic\s+gaussian\s+noise",
            r"paired\s+(?:clean|ground\s*truth|training)",
            r"outliers?|protrusion|heavy[- ]tail",
            r"cannot\s+obtain\s+ground\s*truth",
        ],
        "target_bottleneck": (
            "现有研究过度依赖人工合成零均值高斯噪声，在真实传感器外推有偏 (Protrusion) "
            "与离群点噪声下失效，且缺乏干净真值配对。"
        ),
        "theoretical_rationale": (
            "显式声明非零均值有偏先验，建立基于双次独立噪声采样的自监督 Noise2Noise 一致性协议，"
            "辅以恒等变换基线 (Identity Control) 阻断虚假奖励。"
        ),
        "suggested_method": "noise2noise_prior_aware (结合真实物理噪声模型的无监督验证体系)",
        "falsifiable_claim": (
            "在有偏外推噪声模型下，相场物理滤波能显著降低 Hausdorff 距离，超越无监督恒等基线。"
        ),
        "minimal_experiment": {
            "solver_test": "pf_ac_convex",
            "solver_baseline": "none",
            "compare": "pf_ac_convex vs none",
            "metric": "hausdorff",
            "expected": "pf_smaller",
            "falsification": "若 hausdorff(pf_ac_convex) >= hausdorff(none)，则假设被推翻",
        },
    },
]


def discover_breakthroughs(text: str, paper_id: str = "") -> list[Breakthrough]:
    """从论文正文/摘要中逆向挖掘潜在瓶颈，生成突破点候选清单。"""
    results: list[Breakthrough] = []
    text_clean = text.replace("\r", " ")
    lines = [line.strip() for line in text_clean.split("\n") if len(line.strip()) > 20]

    for item in BREAKTHROUGH_ARCHETYPES:
        matched_quotes = []
        for pat in item["detect_patterns"]:
            rx = re.compile(pat, re.IGNORECASE)
            for line in lines:
                if rx.search(line) and line not in matched_quotes:
                    matched_quotes.append(line[:160])
                    if len(matched_quotes) >= 3:
                        break
            if len(matched_quotes) >= 3:
                break

        if matched_quotes:
            conf = "HIGH" if len(matched_quotes) >= 2 else "MEDIUM"
        else:
            conf = "LOW"

        if conf in ("HIGH", "MEDIUM") or len(results) < 2:
            bt = Breakthrough(
                id=item["id"],
                title=item["title"],
                category=item["category"],
                target_bottleneck=item["target_bottleneck"],
                theoretical_rationale=item["theoretical_rationale"],
                suggested_method=item["suggested_method"],
                evidence_quotes=matched_quotes,
                confidence=conf,
                falsifiable_claim=item["falsifiable_claim"],
                minimal_experiment=item["minimal_experiment"],
            )
            results.append(bt)

    rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    results.sort(key=lambda b: rank.get(b.confidence, 3))
    return results


def breakthrough_markdown(breakthroughs: list[Breakthrough], paper_desc: str = "") -> str:
    """格式化为结构化突破点挖掘报告。"""
    lines = [
        "# 💡 论文突破点挖掘与研究进阶分析报告",
        f"> 分析对象：`{paper_desc or '输入文献'}`　生成候选突破方向：**{len(breakthroughs)}** 条",
        "",
        "---",
        "",
    ]

    for idx, bt in enumerate(breakthroughs, 1):
        badge = {"HIGH": "🔥 高度推荐", "MEDIUM": "⭐ 值得探索", "LOW": "💡 理论线索"}.get(
            bt.confidence, "💡 线索"
        )
        lines.extend([
            f"## {idx}. [{bt.id}] {bt.title}　〔{badge}〕",
            "",
            f"- **所属技术维度**：`{bt.category}`",
            f"- **瞄准的既有瓶颈**：{bt.target_bottleneck}",
            f"- **理论突破机制**：{bt.theoretical_rationale}",
            f"- **建议升级路线**：`{bt.suggested_method}`",
            "",
            "### 🔬 拟立项的可证伪主张与验证实验：",
            f"> **主张**：{bt.falsifiable_claim}",
            "",
            f"- **对比方案**：`{bt.minimal_experiment.get('compare', 'A vs B')}`",
            f"- **核心度量指标**：`{bt.minimal_experiment.get('metric', 'error')}`",
            f"- **认输/推翻判据**：{bt.minimal_experiment.get('falsification', '')}",
            "",
        ])
        if bt.evidence_quotes:
            lines.append("### 📑 原文证据引用（线索出处）：")
            for q in bt.evidence_quotes[:2]:
                lines.append(f"> \"...{q}...\"")
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)
