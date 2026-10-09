"""能力③：实验预注册 + 结果核验。

核心是三态判定：SUPPORTED / REFUTED / **INCONCLUSIVE**。
第三种（被混淆因素支配，当前实验无法区分「方法更差」与「设置不足」）
是最容易被糊弄过去的，也正是本模块存在的理由。
"""
from .prereg import (  # noqa: F401
                     Hypothesis,
                     Prereg,
                     load,
                     new_template,
                     prereg_markdown,
                     save,
                     validate,
)
from .reflect import (
    FailureGradient,
    ReflectionDiagnosis,
    reflect_on_results,
    reflection_markdown,
)
from .verify import HypothesisVerdict, Verdict, verify, verify_file, verify_markdown  # noqa: F401

__all__ = ["Prereg", "Hypothesis", "validate", "load", "save", "new_template",
           "prereg_markdown", "Verdict", "HypothesisVerdict", "verify",
           "verify_file", "verify_markdown", "FailureGradient",
           "ReflectionDiagnosis", "reflect_on_results", "reflection_markdown"]
