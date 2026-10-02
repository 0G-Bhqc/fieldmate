"""把读 JAX-PF 得到的两条新坑写进缺陷库。

为什么是这两条
--------------
`rharness topics` 报「可微求解器 2 篇」，其中一篇是 JAX-PF（2601.06079，
Jian Cao 组，Northwestern，GPL-3.0）。它自带 AC / CH / 耦合 AC-CH 四个 benchmark，
显式 + 隐式两套时间积分。读它的正文，得到两条**本库此前没有**的坑：

1. **显式 vs 隐式不能按墙钟时间比** —— 除非对齐精度。
   原文：「A fair comparison requires enforcing the same accuracy criterion,
   which strongly depends on the admissible time step size... we deliberately
   adopted **smaller** time step sizes for the explicit solver」

   这不是「谁更快」的问题，是**比较本身无效**的问题。JAX-PF 必须给显式更小的
   Δt 才能达到同一精度，然后才比时间。这对所有做格式对比的相场论文都成立，
   而本方向论文普遍不报 Δt（`gaps` 实测缺失率 50~93%）—— 于是这类比较
   在文献里**根本无法复核**。

2. **AD 轨迹长度是独立于算力的约束** —— 可微化特有。
   原文：「Such long trajectories not only increase computational cost but also
   pose severe challenges for **AD**, as reverse-mode AD requires storing the
   whole trajectory」

   也就是说：即使你换 GPU，逆向 AD 存的整条轨迹也不会变短。这解释了为什么
   JAX-PF 要专门做隐式格式 —— 不是为了「隐式更准」，而是为了**缩短轨迹**。
   这一点对导师的拓扑优化线（需要伴随梯度做参数反演）是决定性的。

入库纪律：每条必须有 `evidence`（可核对的原文出处）。
缺陷库 D-REP-001 的教训是「不可核对的结论不进库」。
"""
from __future__ import annotations

import json
from pathlib import Path

LIB = Path("libraries/defect_patterns.jsonl")

NEW = [
    {
        "id": "D-EVA-004",
        "class": "evaluation",
        "severity": "high",
        "status": "verified",
        "title": "显式/隐式格式对比按墙钟时间比是无效的，除非对齐精度",
        "applies_to": ["显式 vs 隐式时间积分对比", "相场格式 benchmark",
                       "GPU vs CPU 实现对比"],
        "symptom": "「隐式快 100 倍」这类结论，但隐式用了更小的时间步或跑到了"
                   "更短的物理时间，两者根本不在同一个精度/时刻上比较。",
        "root_cause": "可接受的时间步由 PDE 本身的性质和格式的稳定性条件决定，"
                      "**不是自由参数**。显式受 Δt 限制只能走小步，隐式可以走大步。"
                      "若不先把两者调到同一精度（或同一物理时刻），时间比没有意义。",
        "detection": "对比表里若出现「explicit / implicit」两行却没有报各自的 Δt、"
                     "步数与终止物理时刻，即判为不可比。代码侧：同一张表里两套求解器"
                     "的时间步长来自不同常数、或终止条件不是同一个 t_final。",
        "evidence": "JAX-PF (arXiv:2601.06079) 原文：「A fair comparison requires "
                    "enforcing the same accuracy criterion, which strongly depends "
                    "on the admissible time step size. These time steps are not "
                    "arbitrary as they are governed by the intrinsic properties of "
                    "the underlying PDEs and stability conditions of the numerical "
                    "scheme. Therefore, to ensure a consistent level of accuracy, we "
                    "deliberately adopted smaller time step sizes for the explicit "
                    "solver in these benchmark studies.」",
        "fix": "对比前先固定终止**物理时刻**与精度容差，两者都用能达到该精度的最大步长；"
               "然后再比时间。若做不到，报告里不要给速度比。",
        "alert_items": ["报时间步", "报稳定条件"],
    },
    {
        "id": "D-DIF-001",
        "class": "differentiable",
        "severity": "high",
        "status": "verified",
        "title": "可微相场里，轨迹长度是独立于算力的约束（逆向 AD 存整条轨迹）",
        "applies_to": ["伴随法/AD 参数反演", "可微相场求解器",
                       "相场拓扑优化", "参数标定"],
        "symptom": "换了 GPU / 加了核，参数标定依然慢得不可用；"
                   "或者隐式格式虽然每步更贵，但整体反而快很多。",
        "root_cause": "逆向模式自动微分需要保存**整条时间轨迹**才能回传梯度。"
                      "显式格式受稳定性条件限制必须走大量小步，轨迹因此极长，"
                      "显存与重算成本随步数线性增长 —— **加算力不缩短轨迹**。"
                      "所以「隐式每步更贵」这个直觉在这里是错的："
                      "隐式能用大步，几步就走完同一段物理时间，轨迹短得多。",
        "detection": "统计一次前向求解的**步数**与单步显存，而不只看总时间。"
                     "若步数 >> 物理时间所需，则瓶颈是轨迹长度而非算力；"
                     "此时加 GPU 无效，应改隐式格式。",
        "evidence": "JAX-PF (arXiv:2601.06079) 原文：「explicit schemes often demand "
                    "an extremely large number of time steps due to stability "
                    "constraints, leading to prohibitively long trajectories when "
                    "simulating microstructure evolution at realistic scales. Such "
                    "long trajectories not only increase computational cost but also "
                    "pose severe challenges for AD, as reverse-mode AD requires "
                    "storing the whole trajectory.」并因此「introduces an implicit "
                    "time-stepping scheme, which allows much larger time steps and "
                    "dramatically shortens the trajectory length for AD.」",
        "fix": "做反演/标定前先量步数与单步显存；步数过大就换隐式格式"
               "（JAX-PF 走的就是这条路），而不是先买算力。",
        "alert_items": ["报时间步", "保体积/守恒"],
    },
]


def main() -> int:
    lines = [l for l in LIB.read_text(encoding="utf-8").splitlines() if l.strip()]
    defs = [json.loads(l) for l in lines]
    by_id = {d["id"]: d for d in defs}

    for d in NEW:
        if d["id"] in by_id:
            print(f"[skip] {d['id']} 已存在")
            continue
        # 字段完整性：缺任何一项都不入库（D-REP-001 纪律）
        missing = [k for k in ("id", "class", "severity", "status", "title",
                               "applies_to", "symptom", "root_cause", "detection",
                               "evidence", "fix") if not d.get(k)]
        if missing:
            print(f"[reject] {d['id']} 缺字段 {missing}")
            return 1
        by_id[d["id"]] = d
        defs.append(d)
        print(f"[add] {d['id']}  {d['title']}")

    LIB.write_text(
        "\n".join(json.dumps(d, ensure_ascii=False, sort_keys=True) for d in defs) + "\n",
        encoding="utf-8")
    print(f"\n缺陷库：{len(defs)} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
