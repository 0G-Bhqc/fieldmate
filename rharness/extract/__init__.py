"""L2 全文五槽抽取 + 渐进式披露阅读卡。"""
from .slots import extract_slots, PaperSlots, Slot, cross_assumptions, SECTION_MAP  # noqa: F401
from .disclose import reading_card_l1, reading_card_l2, card_markdown, PURPOSE_FOCUS  # noqa: F401

__all__ = ["extract_slots", "PaperSlots", "Slot", "cross_assumptions", "SECTION_MAP",
           "reading_card_l1", "reading_card_l2", "card_markdown", "PURPOSE_FOCUS"]