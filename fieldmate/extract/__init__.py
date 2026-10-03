"""L2 全文五槽抽取 + 渐进式披露阅读卡。"""
from .disclose import PURPOSE_FOCUS, card_markdown, reading_card_l1, reading_card_l2  # noqa: F401
from .slots import SECTION_MAP, PaperSlots, Slot, cross_assumptions, extract_slots  # noqa: F401

__all__ = ["extract_slots", "PaperSlots", "Slot", "cross_assumptions", "SECTION_MAP",
           "reading_card_l1", "reading_card_l2", "card_markdown", "PURPOSE_FOCUS"]
