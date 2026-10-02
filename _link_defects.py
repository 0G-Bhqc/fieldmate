"""给缺陷库补 `alert_items`：把「缺陷」和「精进点条目」显式接上。

为什么必须显式写，而不能靠字符串匹配
------------------------------------
`mine_gaps` 里原本是这么找关联缺陷的：

    related = [d for d in by_id.values()
               if item in d.get("detection", "") or d.get("alert_item") == item]

实测结果：**22 条精进点的 related_defects 全部为空**。两个原因都查证过：

  1. 17 条缺陷**没有一条**带 `alert_item` 字段（Counter 实测全是 None）；
  2. `detection` 写的是「怎么查这个缺陷」（例如「扫描源码中形如 dt = c*h*h…」），
     压根不含「报时间步」这类条目名 —— 它测的是运行时行为，不是论文有没有报告。

所以这不是「碰巧没匹配上」，而是这条连接**在结构上恒为空**：
缺陷库描述的是「跑代码时哪里会出错」，精进点描述的是「论文里什么没报告」，
两者本来就隔着一层，而那层从来没被建过。

这层连接是有内容的，不是硬凑
-----------------------------
D-RES-001「时间步量纲随 Laplacian 是否带 ε² 前缀而改变」正是
「报时间步」这个缺口的**具象实例** —— 一个真实的、已实测过的坑。
拿不到这层连接，精进点就只是统计口号；接上之后，
每条精进点都能指向一条已有实测证据的缺陷作为「为什么这条值得做」。

映射按「该缺陷是否真的由这一项缺失导致或放大」来定，不按关键词硬凑。
"""
from __future__ import annotations

import json
from pathlib import Path

LIB = Path("libraries/defect_patterns.jsonl")

# 条目名 -> 缺陷 id。顺序按「机理最直接」排，方便人读。
MAP: dict[str, list[str]] = {
    "报时间步": [
        "D-RES-001",   # 时间步量纲随 ε² 前缀而变（核心）
        "D-SIG-003",   # 论文② 印刷 Δt=0.05h² 与印刷 ε、λ 组合下晶体反而缩小
        "D-RES-002",   # CH 四阶算子的显式步长限制严于 AC
        "D-MOD-004",   # 双阱项再除 ε_w² 使刚性远超显式步承受范围
    ],
    "报稳定条件": [
        "D-RES-002",   # CH 含四阶算子，显式格式的 CFL 是分水岭
        "D-MOD-004",   # 第一步就 overflow，纯属刚性超界
        "D-RES-001",   # 量纲错配会落到「跑得动但什么都不发生」这个更难自查的症状
    ],
    "报分辨率": [
        "D-EVA-001",   # 报精度却不报分辨率下限 -> 结论不可比
        "D-MOD-002",   # 体素化后 1 格厚壳扛不住扩散，几何精度被 h 卡死
        "D-REP-003",   # 亚格点修正量纲错误会让重建整体平移
    ],
    "保体积/守恒": [
        "D-MOD-001",   # 纯 AC（无保真项）腐蚀有限液滴，体积一路缩小
        "D-MOD-003",   # 目标场用 0/1 而非 ±1，晶核被抹平
        "D-MOD-002",   # 壳被抹光后提取的等值面退化为噪声
    ],
    "报噪声模型": [
        "D-EVA-003",   # 合成噪声上训练/标定的方法不可迁移到真实传感器噪声
        "D-REP-005",   # 点级 N2N 配对在密度涨落/抽稀噪声下根本不成立
    ],
    "开源自复现": [
        "D-SIG-001",   # 照印刷公式实现，晶核不生长反收缩
        "D-SIG-002",   # 印刷的 g 表达式有卷积/核系数记号歧义
        "D-SIG-003",   # 照印参数算出与论文图相反的结果
        "D-REP-001",   # 静默兜底掩盖真实失败 —— 没代码就没法自查这一类
    ],
}


def main() -> int:
    lines = [l for l in LIB.read_text(encoding="utf-8").splitlines() if l.strip()]
    defs = [json.loads(l) for l in lines]
    by_id = {d["id"]: d for d in defs}

    # 反查：每个缺陷被哪些条目引用
    rev: dict[str, list[str]] = {}
    for item, ids in MAP.items():
        for did in ids:
            rev.setdefault(did, []).append(item)

    unknown = [d for d in rev if d not in by_id]
    if unknown:
        print(f"错误：映射引用了库里不存在的缺陷 {unknown}")
        return 1

    for d in defs:
        d.pop("alert_item", None)      # 旧字段清掉，避免两套并存
        items = rev.get(d["id"], [])
        if items:
            d["alert_items"] = items

    LIB.write_text(
        "\n".join(json.dumps(d, ensure_ascii=False, sort_keys=True) for d in defs) + "\n",
        encoding="utf-8")

    covered = sum(1 for d in defs if d.get("alert_items"))
    print(f"已写入 {LIB}")
    print(f"  缺陷条目 {len(defs)} 条，其中 {covered} 条带 alert_items")
    print(f"  精进点条目 {len(MAP)} 个：{', '.join(MAP)}")
    orphan = [d["id"] for d in defs if not d.get("alert_items")]
    if orphan:
        print(f"  未挂到任何精进点的缺陷（属正常：它们是实现/工程坑，不是报告规范问题）：")
        print("    " + ", ".join(orphan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
