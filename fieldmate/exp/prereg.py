"""实验预注册（pre-registration）—— 在跑之前把「怎么算验证成功」写死

为什么必须有这一步
------------------
「代码跑通了」和「验证了主张」之间有巨大鸿沟。最贵的失败不是程序崩溃，
而是**跑完了、看着结果、然后改指标让它看起来对**。

预注册把三件事提前锁死：
  1. **主张可证伪**：每条假设必须写出「什么情况下我承认自己是错的」。
  2. **必要对照齐全**：由主张自动推出必须报告的对照项，缺一项就别开跑。
  3. **负结果必须报**：验证器不会因为结论不利于预期就吞掉结果。

时间顺序也是硬约束：预注册文件里有 `created` 时间戳，
验证时会检查「结果文件的 mtime 是否晚于预注册」——晚于才是合法执行，
早于说明你是在看到结果之后才补的注册，标记为 `backfilled`。

这一模块的实际来源
------------------
本项目自己做 pfdenoise 基准时就撞上了：原本预期相场方法在 CD 上赢，
实测输 5~7 倍。当时要么改代码、要么改指标、要么硬着头皮发。
有了预注册 + 本验证器，同样的情况会得到一句可复核的结论：
「H1（保体积）SUPPORTED；H2（CD）REFUTED，但因 res_floor > 报告误差，
结论为 INCONCLUSIVE —— 现有实验无法区分『方法更差』与『分辨率不足』」。
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

__all__ = ["Hypothesis", "Prereg", "validate", "load", "save", "new_template",
           "SUPPORT_TOKENS"]

REQUIRED_TOP = ["id", "created", "claim", "hypotheses", "confounds"]

# 支持（support）标记。刻意做成**可机检**的形式：必须出现
# 「若…则…被推翻 / 不成立 / 视为失败」这类明确认输的措辞，
# 否则说明写的是「验证一下」而不是「可证伪的主张」。
SUPPORT_TOKENS = ["被推翻", "不成立", "视为失败", "证伪", "反驳", "falsif"]

CONFOUND_GUARDS: dict[str, dict[str, str]] = {
    "res_floor": {
        "why": "体素化/网格类 PDE 方法的几何精度被 h/2 卡死。"
               "若报告的误差 <= res_floor，该数字只反映分辨率，"
               "无法用于与方法间比较。",
        "required_when": "结论涉及「体素化/网格方法 vs 非体素方法」的方法精度比较",
        "check": "results 中的 res_floor 存在，且比较涉及的误差 > res_floor",
    },
    "identity_baseline": {
        "why": "「不做任何处理」的基线。缺了它就无法判断指标变化是方法的功劳还是数据本身。",
        "required_when": "任何有监督/无监督评测",
        "check": "results 的 solver 列表中包含 none / identity",
    },
    "noise_model": {
        "why": "噪声的统计性质（零均值/独立/有偏）决定结论可否迁移。"
               "不声明噪声模型时，不同噪声下的排名会翻转。",
        "required_when": "跨方法比较在含噪数据上的表现",
        "check": "results 显式记录了噪声模型及其统计假设",
    },
}


@dataclass
class Hypothesis:
    id: str
    statement: str            # 主张本身（人话）
    metric: str               # 用哪个指标判定
    expected: str             # pf_smaller | pf_larger | equal | exists_counterexample
    falsification: str        # 认输条件（人话，但必须含明确措辞）
    support_required: list[str] = field(default_factory=list)
    compare: str = ""
    note: str = ""
    abs: bool = False        # True = 按绝对值比较（|x| vs |y|）
    observed_default: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# Hypothesis 的合法字段集合。预注册 JSON 里出现未知字段（未来版本加的 key、
# 手写多写的备注）时忽略而不是 TypeError——validate 走 raw dict 不会炸，
# load() 不该是两条解析路径里更脆的那条。
_HYP_FIELDS = frozenset(Hypothesis.__dataclass_fields__)


@dataclass
class Prereg:
    id: str
    created: str
    claim: str
    hypotheses: list[Hypothesis]
    confounds: list[str] = field(default_factory=list)
    mechanism: str = ""
    backend: str = ""
    reproduce: dict[str, Any] = field(default_factory=dict)
    status: str = "registered"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["hypotheses"] = [h.to_dict() for h in self.hypotheses]
        return d

    @staticmethod
    def _hypothesis_from_dict(h: dict[str, Any]) -> Hypothesis:
        dropped = set(h) - _HYP_FIELDS
        if dropped:
            h = {k: v for k, v in h.items() if k in _HYP_FIELDS}
        return Hypothesis(**h)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Prereg:
        hs = [Prereg._hypothesis_from_dict(h) for h in d.get("hypotheses", [])]
        return Prereg(id=d["id"], created=d["created"], claim=d["claim"],
                      hypotheses=hs, confounds=d.get("confounds", []),
                      mechanism=d.get("mechanism", ""), backend=d.get("backend", ""),
                      reproduce=d.get("reproduce", {}), status=d.get("status", "registered"))


# --------------------------------------------------------------------------
def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_template(pid: str = "exp-001") -> dict[str, Any]:
    """空白预注册模板（含本项目真实用过的例子，方便对照）。"""
    return {
        "id": pid,
        "created": _iso_now(),
        "status": "registered",
        "claim": "相场去噪在保体积上优于拉普拉斯平滑",
        "mechanism": "相场的等值面演化不引入系统性体积漂移；拉普拉斯平滑沿法向收缩",
        "hypotheses": [
            {"id": "H1",
             "statement": "|radius_drift(相场)| < |radius_drift(拉普拉斯)|",
             "metric": "radius_drift",
             "compare": "pf_ac_fidelity vs laplacian",
             "expected": "pf_smaller",
             "falsification": "若 |radius_drift(相场)| >= |radius_drift(拉普拉斯)|，H1 被推翻",
             "support_required": ["res_floor"]},
            {"id": "H2",
             "statement": "相场在 Chamfer 距离上不劣于拉普拉斯",
             "metric": "chamfer",
             "compare": "pf_ac_fidelity vs laplacian",
             "expected": "pf_smaller",
             "falsification": "若 chamfer(相场) > chamfer(拉普拉斯)，H2 被推翻",
             "support_required": ["res_floor"],
             "note": "本条预期可能因体素化分辨率而失败。若失败必须先检查 res_floor，"
                     "不得直接归因于方法优劣。"},
        ],
        "confounds": ["体素分辨率", "噪声模型", "点云密度"],
        "backend": "pfdenoise",
        "reproduce": {"cmd": "python ac_2d.py --mode denoise --no-plot", "seed": 0},
    }


def save(prereg: Prereg | dict[str, Any], path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    d = prereg.to_dict() if isinstance(prereg, Prereg) else prereg
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def load(path: str | Path) -> Prereg:
    return Prereg.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


# --------------------------------------------------------------------------
def validate(d: dict[str, Any], results: dict[str, Any] | None = None) -> list[str]:
    """校验预注册是否合格。返回问题列表（空 = 合格）。results 可选：给了就连对照一起查。"""
    problems: list[str] = []

    for k in REQUIRED_TOP:
        if not d.get(k):
            problems.append(f"缺少必填字段 `{k}`")
    if d.get("created") and not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", d["created"]):
        problems.append("`created` 必须是 ISO8601 UTC（如 2026-10-02T16:00:00Z），"
                        "它是「先注册后执行」的唯一时间证据")

    hs = d.get("hypotheses") or []
    if not hs:
        problems.append("没有任何假设 —— 没有假设就没有可证伪的主张")
    for i, h in enumerate(hs, 1):
        tag = h.get("id") or f"#{i}"
        for k in ("statement", "metric", "expected", "falsification"):
            if not h.get(k):
                problems.append(f"假设 {tag} 缺少 `{k}`")
        fz = h.get("falsification", "")
        if fz and not any(t in fz for t in SUPPORT_TOKENS):
            problems.append(
                f"假设 {tag} 的 `falsification` 没有明确的认输措辞"
                f"（需包含 {'/'.join(SUPPORT_TOKENS)} 之一）—— "
                f"「验证一下 X」不是可证伪主张")
        if h.get("expected") not in ("pf_smaller", "pf_larger", "equal",
                                     "exists_counterexample"):
            problems.append(f"假设 {tag} 的 `expected` 取值非法：{h.get('expected')}")
        for req in (h.get("support_required") or []):
            if req not in CONFOUND_GUARDS:
                problems.append(f"假设 {tag} 引用了未知对照项 `{req}`")

    # 主张里出现「精度/误差/更好」却没有要求 res_floor —— 典型的漏项
    txt = d.get("claim", "") + " ".join(h.get("statement", "") for h in hs)
    if re.search(r"(精度|误差|chamfer|cd|优于|更好|更准)", txt, re.I):
        need_res = any("res_floor" in (h.get("support_required") or []) for h in hs)
        if not need_res:
            problems.append(
                "主张涉及方法精度比较，但没有任何假设要求 `res_floor` 对照。"
                "体素化 PDE 方法的误差常被分辨率而非方法本身决定（见本项目实测）")

    if results is not None:
        problems += _check_controls(d, results)
    return problems


def _check_controls(d: dict[str, Any], results: Any) -> list[str]:
    """检查「必要对照是否齐全」。

    results 可能是三种形态，都要认：
      * 完整结果文件 dict（含 rows / res_floor / noise）
      * {"rows": [...]} 解包后的 list
      * 裸 list
    """
    p: list[str] = []
    if isinstance(results, list):
        results = {"rows": results}
    if not isinstance(results, dict):
        return p
    rows = results.get("rows") or []
    solvers = [str(r.get("solver", "")).lower() for r in rows if isinstance(r, dict)]
    if not solvers:
        solvers = [str(s).lower() for s in (results.get("solver")
                                            or results.get("solvers") or [])]
    has_none = any(s in ("none", "identity", "no-op") for s in solvers)
    noise = results.get("noise") or results.get("noise_model")

    for h in d.get("hypotheses", []):
        for req in (h.get("support_required") or []):
            # 早先版本 CONFOUND_GUARDS[req] 裸下标：validate 明明把未知对照项
            # 记为 problem，这里却先一步 KeyError 崩溃 —— 违反「失败显式给建议」。
            g = CONFOUND_GUARDS.get(req)
            if g is None:
                p.append(f"假设 {h.get('id')} 引用了未知对照项 `{req}`"
                         f"（合法值：{', '.join(sorted(CONFOUND_GUARDS))}）")
                continue
            if req == "identity_baseline" and not has_none:
                p.append(f"假设 {h.get('id')} 要求 `{req}`，但结果里没有恒等变换基线。"
                         f"缺了它无法判断指标变化是方法的功劳还是数据本身。")
            if req == "res_floor" and results.get("res_floor") is None \
                    and not any(isinstance(r, dict) and r.get("res_floor") is not None
                                for r in rows):
                p.append(f"假设 {h.get('id')} 要求 `{req}`，但结果里没有该字段。"
                         f"{g['why']}")
            if req == "noise_model" and not noise:
                p.append(f"假设 {h.get('id')} 要求 `{req}`，但结果里没有记录噪声模型。")
    if not d.get("confounds"):
        p.append("没有列出 confound —— 至少要写清「哪些因素可能影响结论」，"
                 "否则负结果到来时无法判断该怪谁")
    return p


def prereg_markdown(d: dict[str, Any], problems: list[str]) -> str:
    L = ["## 预注册校验\n"]
    L.append(f"> `{d.get('id')}`　注册时间 `{d.get('created')}`　"
             f"状态 `{d.get('status', 'registered')}`\n")
    L.append(f"**主张**：{d.get('claim', '(未填)')}\n")
    if d.get("mechanism"):
        L.append(f"**机制**：{d['mechanism']}\n")
    L.append("| # | 假设 | 指标 | 预期方向 | 认输条件 | 必需对照 |")
    L.append("|---|---|---|---|---|---|")
    esc = lambda s: str(s).replace("|", "\\|").replace("\n", " ")   # noqa: E731
    for i, h in enumerate(d.get("hypotheses", []), 1):
        L.append("| {} | {} | `{}` | {} | {} | {} |".format(
            h.get("id", i), esc(h.get("statement", "")), h.get("metric", ""),
            h.get("expected", ""), esc(h.get("falsification", "")),
            ", ".join(f"`{x}`" for x in (h.get("support_required") or [])) or "-"))
        if h.get("note"):
            L.append("| | | | | *注：{}* | |".format(esc(h["note"])))
    L.append(f"\n**已声明的混淆因素**：{', '.join(d.get('confounds', [])) or '(未填)'}\n")

    if problems:
        L.append(f"### ❌ 不合格（{len(problems)} 项）——**不要开始跑实验**\n")
        for x in problems:
            L.append(f"- {x}")
    else:
        L.append("### ✅ 合格，可以开始执行\n")
    L.append("\n---\n")
    L.append("**本步骤的意义**：把「怎么算验证成功」在跑之前锁死。"
             "没有它，负结果到来时唯一的选择就是改指标让它看起来对。")
    return "\n".join(L)
