"""横向对比矩阵与缺陷统计。"""
from .matrix import (  # noqa: F401
                     REPORT_ITEMS,
                     build_matrix,
                     defect_stats,
                     family_of,
                     matrix_csv,
                     matrix_json,
                     matrix_markdown,
)

__all__ = ["build_matrix", "defect_stats", "matrix_markdown", "matrix_json",
           "matrix_csv", "family_of", "REPORT_ITEMS"]
