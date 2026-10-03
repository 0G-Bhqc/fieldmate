"""结果核验：把实测结果对回预注册，给出 SUPPORTED / REFUTED / INCONCLUSIVE

三种结论的区别是本模块的核心价值
----------------------------------
    SUPPORTED      预注册的方向成立
    REFUTED        预注册的方向不成立，**且没有混淆因素能解释它** → 主张被推翻，如实写进论文
    INCONCLUSIVE   方向不成立，**但存在已知混淆因素** → 现有实验**无法区分**
                    「方法更差」与「实验设置不足」

第三种最容易被糊弄过去。真实案例（本项目自己的 pfdenoise 基准）：
    预注册 H2：相场在 Chamfer 上不劣于拉普拉斯
    实测：相场 CD 0.0807 vs 拉普拉斯 0.0160，差 5 倍
    朴素读法：H2 被推翻，相场更差 → 发论文说「相场不行」
    本模块读法：CFL/CFL—— 相场是体素化方法，其误差下限 res_floor ≈ h/2，
                而报告误差已接近该量级，因此「差 5 倍」既可能是方法更差、
                也可能是分辨率不足，**当前实验无法区分**。
                正确动作：报告负结果 + 报告 res_floor + 补一个高密度实验再判。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .prereg import Prereg

__all__ = ["Verdict", "HypothesisVerdict", "verify", "verify_file", "verify_markdown"]


@dataclass
class HypothesisVerdict:
    id: str
    statement: str
    metric: str
    verdict: str = "UNTESTED"         # SUPPORTED / REFUTED / INCONCLUSIVE / UNTESTED
    observed: str = ""
    reason: str = ""
    confound: str = ""
    recommended_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Verdict:
    prereg_id: str
    created: str
    backfilled: bool = False            # 结果早于预注册 = 事后补注册
    hypotheses: list[HypothesisVerdict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def supported(self) -> list[str]:
        return [h.id for h in self.hypotheses if h.verdict == "SUPPORTED"]

    @property
    def refuted(self) -> list[str]:
        return [h.id for h in self.hypotheses if h.verdict == "REFUTED"]

    @property
    def inconclusive(self) -> list[str]:
        return [h.id for h in self.hypotheses if h.verdict == "INCONCLUSIVE"]

    def to_dict(self) -> dict[str, Any]:
        return {"prereg_id": self.prereg_id, "created": self.created,
                "backfilled": self.backfilled, "supported": self.supported,
                "refuted": self.refuted, "inconclusive": self.inconclusive,
                "hypotheses": [h.to_dict() for h in self.hypotheses],
                "notes": self.notes}


def _split_compare(compare: str) -> tuple[str, str] | None:
    """解析 'pf_ac_fidelity vs laplacian' -> ('pf_ac_fidelity','laplacian')。"""
    if not compare or " vs " not in compare:
        return None
    a, b = compare.split(" vs ", 1)
    return a.strip(), b.strip()


def _pick(rows: list[dict], name: str) -> dict | None:
    for r in rows:
        if str(r.get("solver", "")).lower() == name.lower():
            return r
    return None


def _cmp(a: float, b: float, expected: str) -> bool:
    if expected == "pf_smaller":
        return a < b
    if expected == "pf_larger":
        return a > b
    if expected == "equal":
        return abs(a - b) <= 1e-9
    return False


def verify(prereg: Prereg | dict[str, Any], results: list[dict[str, Any]],
           meta: dict[str, Any] | None = None) -> Verdict:
    """把结果对回预注册。

    results: [{solver, chamfer, radius_drift, res_floor, ...}, ...]
    meta:    {created: ISO 时间, res_floor: float, noise: str, ...}
    """
    d = prereg.to_dict() if isinstance(prereg, Prereg) else prereg
    meta = meta or {}
    v = Verdict(prereg_id=d.get("id", "?"), created=d.get("created", ""))

    # 事后补注册检测：结果时间早于预注册时间 -> 不可信
    rc = meta.get("created")
    if rc and d.get("created"):
        v.backfilled = str(rc) < str(d["created"])
        if v.backfilled:
            v.notes.append(
                f"⚠ 结果时间 `{rc}` **早于**预注册时间 `{d['created']}` —— "
                f"这是**事后补注册**。审稿人一眼能看出来，整篇的可信度都要打折。")

    res_floor = meta.get("res_floor")
    if res_floor is None:
        for r in results:
            if r.get("res_floor") is not None:
                res_floor = r["res_floor"]
                break

    for h in d.get("hypotheses", []):
        hv = HypothesisVerdict(id=h.get("id", "?"), statement=h.get("statement", ""),
                               metric=h.get("metric", ""))
        pair = _split_compare(h.get("compare", ""))
        metric = h.get("metric", "")
        if pair is None or not metric:
            hv.verdict = "UNTESTED"
            hv.reason = "无法定位比较对象（`compare` 需为 'A vs B' 格式）或未指定指标"
            v.hypotheses.append(hv)
            continue
        ra, rb = _pick(results, pair[0]), _pick(results, pair[1])
        if ra is None or rb is None or metric not in ra or metric not in rb:
            hv.verdict = "UNTESTED"
            hv.reason = (f"结果里缺少 `{pair[0]}` 或 `{pair[1]}` 的 `{metric}`，"
                         f"无法判定（缺结果不是否定证据）")
            v.hypotheses.append(hv)
            continue
        a, b = float(ra[metric]), float(rb[metric])
        # 假设常写成 |x| < |y|，但指标给的是带符号的值。
        # 不显式取绝对值就会拿 +0.05 和 -0.03 比大小 —— 结论直接反掉。
        # 预注册可用 "abs": true 显式声明；声明了但语句里没有 | 也照取绝对值。
        use_abs = bool(h.get("abs")) or ("|" in (h.get("statement", "")
                                              + h.get("falsification", "")))
        ca, cb = (abs(a), abs(b)) if use_abs else (a, b)
        hv.observed = (f"{metric}: {pair[0]}={a:.6g} vs {pair[1]}={b:.6g}")
        if use_abs:
            hv.observed += f"　（按绝对值比较：{ca:.6g} vs {cb:.6g}）"
        ok = _cmp(ca, cb, h.get("expected", ""))

        # 混淆因素闸门：即使方向成立，若误差量级被分辨率支配，也只能算 INCONCLUSIVE
        floor_blocked = False
        floor_ratio = None
        if res_floor:
            denom = max(abs(ca), abs(cb))
            if denom:
                floor_ratio = denom / res_floor
        for req in (h.get("support_required") or []):
            if req == "res_floor" and res_floor:
                if floor_ratio is not None and floor_ratio <= 1.5:
                    floor_blocked = True
                    hv.confound = (f"res_floor={res_floor:.4g}，两侧误差都在其 1.5 倍以内"
                                   f"（比值 {floor_ratio:.2f}），差异小于分辨率噪声")
                elif use_abs and abs(a) <= 1.5 * res_floor:
                    floor_blocked = True
                    hv.confound = f"res_floor={res_floor:.4g}，本方法误差 {a:.4g} 已在其量级内"

        if floor_blocked:
            hv.verdict = "INCONCLUSIVE"
            hv.reason = (f"预注册方向{'成立' if ok else '不成立'}，但结论被分辨率下限支配："
                         f"{hv.confound}。当前实验**无法区分**「方法更差」与「设置不足」。")
            hv.recommended_action = (
                "1) 论文里如实报告负结果 + 报告 res_floor；"
                "2) 补一个点密度更高（使 res_floor 远小于待比较误差）的实验再判定；"
                "3) 不要因为这一条就把方法优势改写成「精度略逊但保体积更好」——"
                "那是换个指标说同一件事，属于事后改判。")
        elif ok:
            hv.verdict = "SUPPORTED"
            hv.reason = "预注册方向成立，且无已知混淆因素解释"
            if floor_ratio is not None:
                hv.reason += f"；误差为 res_floor 的 {floor_ratio:.1f} 倍，在可分辨范围内"
            hv.recommended_action = "保留为支持性证据；同时报告效应量，别只报方向"
        else:
            hv.verdict = "REFUTED"
            hv.reason = "预注册方向不成立，且无已知混淆因素可解释"
            if floor_ratio is not None:
                hv.reason += (f"；误差为 res_floor 的 {floor_ratio:.1f} 倍，"
                              f"差异不是分辨率噪声")
            hv.recommended_action = ("**如实写入论文的负结果。** "
                                     "先检查实现是否有缺陷（对照缺陷库 D-REP-*），"
                                     "确认无误后再下结论；不要改指标或换基线。")
        v.hypotheses.append(hv)

    if not v.hypotheses:
        v.notes.append("预注册里没有任何假设，等于没有验证计划")
    if v.supported and v.refuted:
        v.notes.append("出现**部分支持、部分推翻**的混合结果——这是常态，"
                       "论文里要分别陈述，不要只报支持的那几条。")
    if not v.supported and not v.refuted and not v.inconclusive:
        v.notes.append("没有任何假设得到判定：检查结果数据是否完整")
    return v


def verify_file(prereg_path: str | Path, results_path: str | Path,
                meta: dict[str, Any] | None = None) -> Verdict:
    d = json.loads(Path(prereg_path).read_text(encoding="utf-8"))
    res = json.loads(Path(results_path).read_text(encoding="utf-8"))
    rows = res["rows"] if isinstance(res, dict) and "rows" in res else res
    m = dict(meta or {})
    if isinstance(res, dict):
        for k in ("res_floor", "noise", "created", "solver", "solvers"):
            if k in res:
                m.setdefault(k, res[k])
    m.setdefault("created", _mtime_iso(results_path))
    return verify(d, rows, m)


def _mtime_iso(p: str | Path) -> str:
    from datetime import datetime, timezone
    ts = os.path.getmtime(str(p))
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _md(s: str) -> str:
    """markdown 表格单元格转义。

    假设文本里到处是 `|radius_drift(x)|` 这种竖线，**不转义会把整张表冲散**。
    这不是洁癖：数学记号里的绝对值符号在表格里是真会出事的。
    """
    return str(s).replace("|", "\\|").replace("\n", " ")


def verify_markdown(v: Verdict, d: dict[str, Any]) -> str:
    L = ["## 结果核验（对照预注册）\n"]
    L.append(f"> 预注册 `{v.prereg_id}`　注册于 `{v.created}`\n")
    if v.backfilled:
        L.append("> ⚠ **事后补注册**——结果早于预注册，可信度受损。\n")
    L.append(f"**主张**：{_md(d.get('claim', ''))}\n")
    L.append(f"**结论**：✅ 支持 {len(v.supported)} 条　"
             f"❌ 推翻 {len(v.refuted)} 条　"
             f"⚠ 无法判定 {len(v.inconclusive)} 条\n")
    L.append("| # | 假设 | 判定 | 实测 | 说明 |")
    L.append("|---|---|---|---|---|")
    icon = {"SUPPORTED": "✅ SUPPORTED", "REFUTED": "❌ REFUTED",
            "INCONCLUSIVE": "⚠ INCONCLUSIVE", "UNTESTED": "· UNTESTED"}
    for h in v.hypotheses:
        L.append("| {} | {} | {} | {} | {} |".format(
            _md(h.id), _md(h.statement), icon.get(h.verdict, h.verdict),
            _md(h.observed or "-"), _md(h.reason or "-")))
    L.append("\n### 下一步该做什么\n")
    for h in v.hypotheses:
        if h.recommended_action:
            L.append(f"- **{h.id}**（{icon.get(h.verdict, h.verdict)}）：{h.recommended_action}")
    if v.notes:
        L.append("\n### 核验器附加提醒\n")
        for n in v.notes:
            L.append(f"- {n}")
    L.append("\n---\n")
    L.append("**负结果也要写进论文。** 预注册的价值不在于让结果好看，"
             "而在于让别人能复核「你为什么相信（或不相信）这个结论」。")
    return "\n".join(L)
