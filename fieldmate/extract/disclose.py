"""渐进式披露阅读卡 —— 解决「所有论文都被同样对待」的浪费

核心观察
--------
LLM 论文助手最大的浪费：对每篇论文都给同样的均匀深度。
但你从一篇论文里需要什么，**取决于你读它干什么**：

    implement 复现 → 需要 离散格式 / 参数表 / 初值 / Δt / 边界条件
    beat    超越 → 需要 Assumption / Protocol / 缺陷库匹配结果
    cite    引用 → 需要 Claim / 刊物 / 定位
    build-on承接 → 需要 接口约定 / 作者留的 open question

阅读卡因此分两层：
    L1 一屏读完（Claim + 各槽命中数 + 最高优先级的 Assumption）
    L2 按需展开（每个槽的实际句子 + 所在章节）
本模块负责生成 L1 与 L2，默认只给 L1。
"""

from __future__ import annotations

from typing import Any

from .slots import PaperSlots, Slot

__all__ = ["reading_card_l1", "reading_card_l2", "card_markdown"]

PURPOSE_FOCUS: dict[str, tuple[str, ...]] = {
    "implement": ("Mechanism", "Protocol"),
    "beat": ("Assumption", "Gap", "Protocol"),
    "cite": ("Claim",),
    "build-on": ("Assumption", "Gap"),
}

NO_SLOT_ICON = "·"


def reading_card_l1(ps: PaperSlots) -> dict[str, Any]:
    assum = ps.slots.get("Assumption", Slot())
    gap = ps.slots.get("Gap", Slot())
    return {
        "paper_id": ps.paper_id,
        "title": ps.title,
        "n_chars": ps.n_chars,
        "sections": ps.sections,
        "claim": (ps.slots.get("Claim", Slot()).items[0]["sentence"]
                  if ps.slots.get("Claim", Slot()).items else None),
        "slot_counts": {k: len(v.items) for k, v in ps.slots.items()},
        "top_assumption": assum.items[0]["sentence"] if assum.items else None,
        "top_gap": gap.items[0]["sentence"] if gap.items else None,
        "status": ("未取到全文" if not ps.n_chars else "ok"),
    }


def reading_card_l2(ps: PaperSlots, slots: tuple[str, ...] | None = None) -> dict[str, Any]:
    out = {"paper_id": ps.paper_id, "title": ps.title, "sections": ps.sections}
    for name in (slots or tuple(ps.slots)):
        s = ps.slots.get(name)
        out[name] = [i["sentence"] for i in s.items] if s else []
    return out


def _esc(s: str) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def card_markdown(cards: list[dict[str, Any]], purpose: str = "beat",
                  l2: bool = False) -> str:
    """阅读卡 Markdown。l2=True 时按 purpose 展开重点槽。"""
    focus = PURPOSE_FOCUS.get(purpose, PURPOSE_FOCUS["beat"])
    L = [f"## 论文阅读卡（阅读目的：`{purpose}`）\n"]
    L.append(f"> 重点槽：{', '.join(focus)}　"
             f"{'（已展开 L2 细节）' if l2 else '（仅 L1 摘要，按需再加 --l2）'}\n")
    for c in cards:
        L.append(f"### {c['title'][:110]}\n")
        L.append(f"`{c['paper_id']}`　{c['n_chars']} 字符　"
                 f"章节：{', '.join(c.get('sections') or []) or '未识别'}\n")
        if c.get("claim"):
            L.append(f"**Claim**：{_esc(c['claim'])}\n")
        sc = c.get("slot_counts", {})
        L.append("| 槽 | 命中 | 关注 |")
        L.append("|---|---|---|")
        for k, n in sc.items():
            L.append(f"| {k} | {n} | {'★' if k in focus else ''} |")
        L.append("")
        if c.get("top_assumption"):
            L.append(f"**最可能的精进点（Assumption 句）**：{_esc(c['top_assumption'])}\n")
        if c.get("top_gap"):
            L.append(f"**作者自述局限（Gap 句）**：{_esc(c['top_gap'])}\n")
    if l2:
        L.append("\n---\n## L2 细节（按阅读目的展开）\n")
        for c in cards:
            L.append(f"### {c['title'][:90]}\n")
            for s in focus:
                L.append(f"**{s}**：\n")
                for sent in c.get(s, []) or ["（未命中）"]:
                    L.append(f"  - {_esc(sent)}")
                L.append("")
    L.append("---\n")
    L.append("**这些是候选句，不是判定。** 假设句必须人读："
             "「we assume the material is homogeneous」和「we assume readers know PDEs」"
             "的学术价值天差地别，正则判不了 —— 这正是「判定归脚本、判断归人」的边界。")
    return "\n".join(L)
