"""修一条自相矛盾：我刚往缺陷库加的条目，违反了自己定的入库规矩。

问题
----
`SKILL.md` 明写「新增一条缺陷时**两者都要改**（知识库 + 可执行规则），
没有可执行检测方式的条目不准入库」。我上一轮加了 D-EVA-004 与 D-DIF-001，
只写了 `detection` 字段，**没加规则**。

更深一层：那 4 条「无规则」的缺陷**根本不是同一类**，而 `patterns` 把它们
混在一张列表里报出来 —— 这是个更实际的问题：

    D-EVA-004  显式/隐式对比无效      -> **可以从论文文本检出**（我该写规则，忘了）
    D-DIF-001  AD 轨迹长度            -> 跑代码数步数才看得见，文本里查不到
    D-REP-003  亚格点修正量纲错误     -> 同上
    D-REP-004  有向 Hausdorff 索引复用 -> 同上

混在一起的坏处很具体：读 `missing_rules` 的人要么给「跑代码才看得见」的缺陷
硬写一条永远不触发的文本规则（制造假信号），要么整张列表当真话忽略。
**两个方向都是自伤。**

做法
----
1. 给 D-EVA-004 补一条真能用的文本规则（explicit/implicit 并提却未报 Δt）。
2. 给运行时缺陷标 `detect_by: "runtime"`，并在 `patterns` 里分成两栏：
   - `missing_rules`         缺文本规则 —— **该做而没做**，可行动
   - `runtime_only`          按设计没有文本规则 —— 不是欠账
3. 规则本身要过精度体检，不许引入假阳性。
"""
from __future__ import annotations

import json
from pathlib import Path

LIB = Path("libraries/defect_patterns.jsonl")
RULES = Path("contracts/detection_rules.json")

# 运行时缺陷：判据在代码执行里，论文文本里查不到。标出来，别混进「该做而没做」。
RUNTIME_ONLY = {
    "D-DIF-001": "判据是一次前向求解的**步数**与单步显存，只能跑代码得到。",
    "D-REP-003": "判据是重建结果整体平移、Chamfer≈包围盒尺度，只能跑代码得到。",
    "D-REP-004": "判据是有向 Hausdorff 距离在两侧复用同一近邻索引，只能跑代码得到。",
}

# 补一条真能用的文本规则。注意精度：只在**显式与隐式被并列比较**时触发，
# 且要求全文没报任何时间步形式 —— 避免「只是提到了 implicit」就命中。
NEW_RULE = {
    "id": "D-EVA-004",
    "applies_if": {
        "present": [
            ["any", r"\b(phase[- ]field|相场|Allen[- ]Cahn|Cahn[- ]Hilliard)\b"],
        ]
    },
    "signals": [
        {
            "type": "present",
            "scope": "any",
            "pattern": r"\b(?:explicit|implicit)\b",
            "note": "提到显式/隐式时间积分",
        },
        {
            "type": "present",
            "scope": "any",
            "pattern": r"\b(?:explicit\s+(?:and|vs\.?|versus)\s+implicit|"
                       r"implicit\s+(?:and|vs\.?|versus)\s+explicit|"
                       r"compar\w+\s+(?:of|between)\s+[^.]{0,40}(?:explicit|implicit))\b",
            "note": "把显式与隐式**并列比较**",
        },
        {
            "type": "absent",
            "scope": "any",
            "pattern": r"\b(Delta ?t|\\Delta ?t|Δt|∆t|dt\s*=|time step|timestep|"
                       r"step size|时间步)\b",
            "note": "并列比较了显式/隐式却【未】报时间步——D-EVA-004 的核心信号。"
                    "没有可比的时间步，速度比不成立。",
        },
    ],
}


def main() -> int:
    # ---- 1) 标运行时缺陷
    lines = [l for l in LIB.read_text(encoding="utf-8").splitlines() if l.strip()]
    defs = [json.loads(l) for l in lines]
    by_id = {d["id"]: d for d in defs}
    for did, why in RUNTIME_ONLY.items():
        if did not in by_id:
            print(f"[skip] {did} 不在库里")
            continue
        by_id[did]["detect_by"] = "runtime"
        by_id[did]["runtime_reason"] = why
        print(f"[mark] {did} -> runtime")
    LIB.write_text(
        "\n".join(json.dumps(d, ensure_ascii=False, sort_keys=True) for d in defs) + "\n",
        encoding="utf-8")

    # ---- 2) 补 D-EVA-004 的规则
    rules = json.loads(RULES.read_text(encoding="utf-8"))
    ids = {r["id"] for r in rules["rules"]}
    if "D-EVA-004" in ids:
        print("[skip] D-EVA-004 已有规则")
    else:
        rules["rules"].append(NEW_RULE)
        RULES.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[add] D-EVA-004 规则（规则数 {len(ids)} -> {len(ids) + 1}）")

    # ---- 3) 自检：规则引用的缺陷必须存在，且文本规则的缺陷不能标 runtime
    lib_ids = set(by_id)
    for r in rules["rules"]:
        assert r["id"] in lib_ids, f"规则引用了库里没有的缺陷：{r['id']}"
    print("\n自检通过：所有规则都能对上库里的条目。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
