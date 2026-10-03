"""精进点生成（能力①的核心输出）

输入：横向对比结果
输出：**可执行的精进点候选**，每个带证据强度分级与最小验证实验

为什么要单独一层
----------------
横向对比只回答「哪里有缺失」，而研究要回答的是「所以我能做什么」。
中间这一步如果不用机器做，新人就会卡在这里 —— 他能看到表格里有 7 篇没报时间步，
但不知道这算不算一个机会、更不知道该怎么把它变成一篇论文。

本模块把这一步显式化，并且**拒绝输出没有证据支撑的精进点**。

信号强度分级（本模块最重要的部分）
--------------------------------
arXiv 摘要是 150~250 词的极短文本。**没人会在摘要里写 Δt、ε、网格分辨率。**
所以「摘要中未提及时间步」几乎必然为真 —— 它测的是「摘要这种体裁会不会写这个」，
不是「论文有没有报告这个」。

如果不做分级，直接把「7/7 篇未报时间步」当发现发出去，就是**拿体裁特征冒充领域缺陷**
—— 这正是本框架自己条目 D-REP-001 警告的「结论反向」。

分级：
    STRONG  全文级信号支持（PDF 全文已解析）
    MEDIUM  摘要级信号 + 高基线缺失率 + 与已知缺陷的机理一致
    WEAK    仅摘要级信号，缺失率高但**可能只是体裁使然**（默认档，必须全文验证才能用）
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

# 体裁敏感度：某项信息在【摘要】这种体裁里出现的天然概率。
# high   = 摘要里几乎一定会写（所以"未提及"是真的强信号）
# medium = 有时写
# low    = 摘要里基本不会写（所以"未提及"几乎没有信息量）
GENRE_SENSITIVITY: dict[str, str] = {
    "报分辨率":     "low",      # 几乎不进摘要
    "报噪声模型":   "low",
    "报时间步":     "low",
    "报稳定条件":   "low",
    "保体积/守恒":  "medium",   # 常写成 "volume-preserving"，摘要里可能提
    "开源自复现":   "medium",   # 摘要常写 "code will be released"
}

# 机理链：为什么"缺这一项"是一个真问题（这是精进点的推理核心）
RATIONALE: dict[str, str] = {
    "报分辨率": (
        "体素化/网格类方法的几何精度被网格步长 h 卡死，n_vox ∝ √(点密度)。"
        "不报分辨率时，不同分辨率下的精度数字不可横向比较，"
        "「方法 A 优于方法 B」的结论可能只反映了二者的网格设置差异。"
    ),
    "报噪声模型": (
        "噪声的统计性质（是否零均值、是否逐点独立、是否有偏）直接决定"
        "「训练对分布」的作用 [arXiv:2609.16788 结论：起作用的是配对分布而非损失]。"
        "不报噪声模型时，无监督结果的不可迁移性无法评估。"
    ),
    "报时间步": (
        "时间步的选取依赖稳定性条件，而 ε 与 Δt 的量纲关系随 Laplacian 是否带 ε² 前缀而变。"
        "不报时间步时，复现者极可能因量纲错配而「跑得出结果但什么都不发生」。"
    ),
    "报稳定条件": (
        "显式格式的 CFL 条件是能否复现的分水岭。本方向已实测："
        "照抄错误的 Δt 量纲会让界面几百步只动 0.01，"
        "症状是「程序在跑但什么都不发生」——极难自查。"
    ),
    "保体积/守恒": (
        "去噪类方法的核心卖点常是保体积（双阱势梯度流 / Cahn–Hilliard 守恒律）。"
        "不报漂移量时无法区分「保体积」与「恰好看起来还行」。"
    ),
    "开源自复现": (
        "无可获取实现 = 结论不可被第三方复核。本条目自身就是教训："
        "我们曾把「实现 bug 导致的指标异常」误读为「方法无效」。"
    ),
}

# 最小验证实验模板：每个精进点必须能落到一个具体实验上，否则不算精进点
MIN_EXPERIMENT: dict[str, str] = {
    "报分辨率": (
        "取该方向 20~50 篇体素化 PDE 论文，逐篇记录 h、N、点密度、报告的误差指标；"
        "计算误差与 h 的相关性。若误差 ∝ h，则证明「未报分辨率」使现有横向比较失效。"
        "工具：pfdenoise（已自带 res_floor 与 at_floor 机制）。"
    ),
    "报噪声模型": (
        "固定去噪器，在同一份数据上用 5 种统计性质不同的噪声（零均值高斯 / 有偏凸起 / "
        "各向异性 / 离群 / 密度涨落）各跑一遍，测量性能排序是否翻转。"
        "若排序翻转，则证明「不声明噪声模型」的比较不可迁移。工具：pfdenoise noise。"
    ),
    "报时间步": (
        "在开源实现上做「Δt 量纲」消融：按 ε²Δφ 与裸 Δφ 两种解释各跑一次，"
        "测量界面演化量。预期：正确解释下演化显著，错误解释下几乎不动。"
    ),
    "报稳定条件": (
        "对开源实现做 CFL 扫描，找出「跑得动但不收敛」与「发散」的分界，"
        "证明稳定条件缺失是复现失败的首要原因。"
    ),
    "保体积/守恒": (
        "在同一噪声强度下比较保体积类（CH、AC+保真）与收缩类方法（Laplacian、bilateral）"
        "的体积漂移与表面误差，绘制精度-体积帕累托前沿。"
    ),
    "开源自复现": (
        "对未开源但有完整补充材料的论文尝试复现，记录失败点；"
        "统计失败原因中「参数/格式未说明」占比。"
    ),
}


@dataclass
class Gap:
    """一个精进点候选。"""

    item: str
    strength: str                    # STRONG / MEDIUM / WEAK
    reported: int
    total: int
    missing_rate: float
    genre_sensitivity: str
    rationale: str
    min_experiment: str
    related_defects: list[str] = field(default_factory=list)
    fulltext_backed: bool = False
    caveats: list[str] = field(default_factory=list)
    missing_with_fulltext: int = 0
    missing_total: int = 0
    # 出处。`missing_sample` 只收**有全文**的条目 —— 依据是它们才构成证据。
    # 没有名单，缺失率就只是一个无法回查的数字。
    missing_sample: list[dict[str, Any]] = field(default_factory=list)
    reported_sample: list[dict[str, Any]] = field(default_factory=list)
    # 这条缺失率是在**哪个子群里**算出来的。
    #
    # 为什么不能省：mine_gaps 按 (family, item) 循环，同一个 item 会在每个
    # 族里各出一条。实测 247 篇语料上「开源自复现」出现 5 次、分母分别是
    # 68/25/123/15/12 —— 缺了 family，这五条长得一模一样却分母不同，
    # 读者既不知道该信哪个，也无法回去核对任何一条。
    # 「0/12 全缺」和「0/123 全缺」是完全不同的两句话，不能并排放着不标注。
    family: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "item": self.item, "family": self.family, "strength": self.strength,
            "reported": self.reported, "total": self.total,
            "missing_rate": round(self.missing_rate, 3),
            "missing_with_fulltext": self.missing_with_fulltext,
            "missing_total": self.missing_total,
            "genre_sensitivity": self.genre_sensitivity,
            "rationale": self.rationale, "min_experiment": self.min_experiment,
            "related_defects": self.related_defects,
            "fulltext_backed": self.fulltext_backed, "caveats": self.caveats,
            "missing_sample": self.missing_sample,
            "reported_sample": self.reported_sample,
        }

    @property
    def headline(self) -> str:
        return (f"{self.family} · {self.item}：{self.reported}/{self.total} 提及"
                f"（缺失 {self.missing_rate:.0%}）· 证据强度 {self.strength}")


def _classify(rate: float, genre: str, fulltext_ratio: float, n: int) -> str:
    """信号强度分级。宁可低估，不可高估。"""
    if fulltext_ratio >= 0.8:
        return "STRONG"                       # 全文支撑
    if genre == "high":
        return "STRONG"
    if n < 5:
        return "WEAK"                         # 样本太少
    if genre == "medium" and rate >= 0.7:
        return "MEDIUM"
    if genre == "low":
        return "WEAK"                         # 摘要体裁几乎不写 -> 无信息量
    return "MEDIUM"


def _related_defects(by_id: dict[str, dict[str, Any]], item: str) -> list[str]:
    """哪些缺陷条目是这个精进点的**具象实例**。

    两种写法都认，兼容库里新旧两种格式：
      * `alert_items`: 列表，一条缺陷可挂多个条目（当前格式）
      * `alert_item` : 单值（旧格式，保留以防外部库还在用）

    早先这里只有 `item in d.get("detection", "")` 一条路，实测 22 条精进点的
    related_defects **全部为空**：库里 17 条没有一条带 alert_item，而 detection
    写的是「怎么查这个缺陷」（扫源码里的 `dt = c*h*h` 之类），本来就不该包含
    「报时间步」这种条目名 —— 拿一个字段干两个活，连接在结构上恒为空。
    """
    out: list[str] = []
    for d in by_id.values():
        items = d.get("alert_items")
        hit = (item in items) if isinstance(items, list) else (d.get("alert_item") == item)
        if hit:
            out.append(d["id"])
    return out


def mine_gaps(rows: list[Any], stats: dict[str, Any],
              library: list[dict[str, Any]] | None = None,
              min_rate: float = 0.5, min_n: int = 5,
              max_cites: int = 5) -> list[Gap]:
    """从横向对比结果里挖出精进点候选。

    刻意**只输出高缺失率**的项：缺失率低说明这个方向已经做对了，
    不是机会。真正的机会在"集体没做"的地方。
    """
    library = library or []
    by_id = {d["id"]: d for d in library}
    n = len(rows)
    if n == 0:
        return []
    ft_ratio = sum(1 for r in rows if getattr(r, "has_fulltext", False)) / n

    # 家族 × 报告项：找出「某族里集体缺失」的组合
    fam_items: dict[tuple[str, str], list] = defaultdict(list)
    for r in rows:
        for item, ok in r.reported.items():
            if not ok:
                fam_items[(r.family, item)].append(r)

    out: list[Gap] = []
    for (fam, item), miss in fam_items.items():
        grp = [r for r in rows if r.family == fam]
        if len(grp) < min_n:
            continue
        rate = len(miss) / len(grp)
        if rate < min_rate:
            continue
        genre = GENRE_SENSITIVITY.get(item, "low")

        # 出处：谁缺了、谁报了。**缺了名单是这条精进点唯一能被复核的东西。**
        #
        # 借 OpenScholar 的做法（arXiv 2411.14199 / Nature 2026-02）：
        # 它的核心卖点是「引文必须可核查」，因为 GPT-4o 有 78~90% 的概率编造引文。
        # 反过来看本项目的报告：每条精进点都说得出机理、挂得上缺陷条目，
        # 却**说不出「这个 100% 缺失是哪些论文造成的」**。
        # 读者只能接受一个百分比，无法回去核对任何一篇 ——
        # 对一个专门揭露「论文没报什么」的工具，这是最要命的短板。
        keep = [r for r in miss if getattr(r, "has_fulltext", False)]
        missing_sample = [{"paper_id": r.paper_id, "year": r.year,
                           "title": r.title[:90], "venue": r.venue}
                          for r in keep[:max_cites]]
        reported_sample = [{"paper_id": r.paper_id, "year": r.year,
                            "title": r.title[:90]}
                           for r in grp if r.reported.get(item)][:max_cites]

        # 强度按【缺失的那几篇里有多少有全文】来定，而不是按整个语料。
        # 理由：有一篇论文的全文却仍未提及某项，才是「真·未报告」的证据；
        # 只有摘要而摘要没写，几乎没有信息量（体裁使然）。
        # 早先版本用语料级全文率，把这两种情况混为一谈，
        # 结果是全文覆盖 71% 时仍然判不出任何 STRONG —— 过于保守且不精确。
        miss_ft = sum(1 for r in miss if getattr(r, "has_fulltext", False))
        backed = miss_ft / len(miss) if miss else 0.0
        strength = _classify(backed, genre, backed, len(miss))
        related = _related_defects(by_id, item)
        gaps = Gap(
            item=item, family=fam, strength=strength,
            reported=len(grp) - len(miss), total=len(grp),
            missing_rate=rate, genre_sensitivity=genre,
            rationale=RATIONALE.get(item, ""),
            min_experiment=MIN_EXPERIMENT.get(item, ""),
            related_defects=related,
            fulltext_backed=backed >= 0.8,
            missing_with_fulltext=miss_ft, missing_total=len(miss),
            missing_sample=missing_sample,
            reported_sample=reported_sample,
        )
        gaps.caveats = _caveats(item, genre, strength, backed, rate, ft_ratio)
        out.append(gaps)

    # 排序：强度优先，其次全文支撑度，再次缺失率
    rank = {"STRONG": 0, "MEDIUM": 1, "WEAK": 2}
    return sorted(out, key=lambda g: (rank[g.strength], -g.fulltext_backed,
                                      -g.missing_rate, -g.total))


def _caveats(item: str, genre: str, strength: str, backed: float,
             rate: float, ft_ratio: float) -> list[str]:
    """生成这条精进点自身的局限。

    每一条都必须**真实**：早先版本把缺失率硬编码成 100%，
    而观察里写的是 86% —— 报告自己和自己矛盾。
    对一个专门揭露「未报告 ≠ 未做」的工具来说，自己在报告里说错数字
    是最致命的自伤，必须逐条对齐真实取值。
    """
    c = [f"arXiv 覆盖不全：{item} 的统计只代表 arXiv 子集，不能外推到全领域。"]
    if backed >= 0.8:
        c.append(f"**证据基础扎实**：缺失的条目中有 {backed:.0%} 解析了全文，"
                 f"「未提及」是全文层面的判断，不是摘要体裁使然。")
    elif backed > 0:
        c.append(f"⚠ 缺失的条目里只有 {backed:.0%} 有全文；"
                 f"其余仅凭摘要判定，可能只是「摘要这种体裁不写它」。")
    else:
        c.append("⚠ 缺失的条目**全部只有摘要**。摘要通常不写 Δt/分辨率/噪声模型，"
                 "所以本项缺失率主要反映「摘要会不会写」，**不可作为结论**。")
    if genre == "low":
        c.append(f"⚠ 体裁敏感：「{item}」属于摘要几乎不会写的类型，"
                 f"即使有全文也要人工复核段落位置（参数常只在正文表格里）。")
    if ft_ratio < 0.8:
        c.append(f"本批语料全文率 {ft_ratio:.0%}，未取到全文的论文其信号不可用。")
    if strength == "WEAK":
        c.append("证据强度 WEAK：按本工具自己的纪律，WEAK 级信号**不可作为立项依据**，"
                 "只能作为「值得去查」的线索。")
    return c


def gaps_markdown(gaps: list[Gap], query: str = "", n_papers: int = 0) -> str:
    L = ["## 精进点候选\n"]
    distinct = len({g.item for g in gaps})
    n_fams = len({g.family for g in gaps})
    L.append(f"> 检索式：`{query}`　覆盖 {n_papers} 篇　"
             f"候选 {len(gaps)} 条 = {distinct} 个条目 × {n_fams} 个族\n")
    strong = [g for g in gaps if g.strength == "STRONG"]
    weak = [g for g in gaps if g.strength == "WEAK"]
    L.append(f"**可作为立项依据的（STRONG）：{len(strong)} 条**　"
             f"**仅作线索的（WEAK）：{len(weak)} 条**\n")
    L.append("> 每条都标注了它所属的**族**。同一个条目在不同族里的分母不同，"
             "「0/12 全缺」和「0/123 全缺」不是一回事，脱离分母的缺失率没有意义。\n")
    if not strong:
        L.append("> ⚠ **本轮没有 STRONG 级精进点。** "
                 "按纪律，此时**不应该**基于本报告立项——"
                 "先做全文级复核（见下方 WEAK 项的 caveats），或扩大样本量。\n")

    for i, g in enumerate(gaps, 1):
        icon = {"STRONG": "✅", "MEDIUM": "🟡", "WEAK": "⚪"}[g.strength]
        L.append(f"### {i}. {icon} {g.item}　〔族：{g.family}〕\n")
        L.append(f"**观察**：{g.reported}/{g.total} 篇在标题/摘要/全文中提及，"
                 f"缺失率 {g.missing_rate:.0%}　"
                 f"（体裁敏感度 `{g.genre_sensitivity}`）\n")
        L.append(f"**证据基础**：缺失的 {g.missing_total} 篇里，"
                 f"{g.missing_with_fulltext} 篇**已解析全文**"
                 f"（{g.missing_with_fulltext/max(g.missing_total,1):.0%}）\n")
        L.append(f"**为什么是问题**：{g.rationale}\n")
        L.append(f"**最小验证实验**：{g.min_experiment}\n")
        if g.related_defects:
            L.append(f"**关联缺陷**：{', '.join('`'+d+'`' for d in g.related_defects)}\n")
        if g.missing_sample:
            L.append(f"**出处（未报告该项的论文，前 {len(g.missing_sample)} 篇 / "
                     f"共 {g.missing_total} 篇）**：\n")
            for c in g.missing_sample:
                y = f"{c['year']}　" if c.get("year") else ""
                L.append(f"- `{c['paper_id']}`　{y}{c['title']}")
            L.append("\n> 这份名单是本条精进点**唯一能被逐篇复核的东西**。"
                     "只列有全文的条目 —— 依据只来自看过原文的论文。\n")
        if g.reported_sample:
            L.append("**对照（报告了该项的）**："
                     + "、".join(f"`{c['paper_id']}`" for c in g.reported_sample) + "\n")
        L.append("**这条精进点的自身局限**：\n")
        for c in g.caveats:
            L.append(f"- {c}")
        L.append("")
    return "\n".join(L)


def gaps_json(gaps: list[Gap], query: str = "", n_papers: int = 0) -> str:
    import json
    return json.dumps({
        "query": query, "n_papers": n_papers, "n_candidates": len(gaps),
        # 候选条数是 (族 × 条目) 的组合数；去重后的条目数才是「有几个不同的机会」。
        # 两个数都报，否则 22 条候选会被误读成 22 个机会（实际只有 6 个）。
        "n_distinct_items": len({g.item for g in gaps}),
        "families": sorted({g.family for g in gaps}),
        "n_strong": sum(1 for g in gaps if g.strength == "STRONG"),
        "n_weak": sum(1 for g in gaps if g.strength == "WEAK"),
        "discipline": "WEAK 级信号不可作为立项依据；STRONG 需全文级复核",
        "gaps": [g.to_dict() for g in gaps],
    }, ensure_ascii=False, indent=2)
