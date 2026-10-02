"""横向对比矩阵与缺陷统计。"""
from .matrix import (build_matrix, defect_stats, matrix_markdown,  # noqa: F401
                     matrix_json, matrix_csv, family_of, REPORT_ITEMS)

__all__ = ["build_matrix", "defect_stats", "matrix_markdown", "matrix_json",
           "matrix_csv", "family_of", "REPORT_ITEMS"]