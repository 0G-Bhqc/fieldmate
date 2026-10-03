# 任务：Assumption 槽精筛（assumption_refine）

你会收到一篇（或几段）计算科学论文正文中**命中假设类词表**的句子（`data.sentences`）。
请逐句判断：它是不是一条**值得记录的科学假设**——即论文把某个前提当作成立来用，
却没有（或不打算）在文中验证它。

## 判什么、不判什么

**判**（对复现/超越该论文有用）：
- 模型假设：把某个物理/数学前提当成立（"we assume the material is homogeneous"、
  "the noise is i.i.d. Gaussian"、"忽略表面对流损失"）
- 范围假设：把适用范围当成立（"we restrict ourselves to 2D"、"for small perturbations"）
- 数值假设：把数值前提当成立（"the scheme is stable for the chosen Δt"，
  "grids are fine enough that discretization error is negligible"）
- 先验假设：默认读者/后续工作接受但未验证的选择（"following [12], we take λ=1"）

**不判**（丢掉，不要输出）：
- 纯数学推导里的"假设 x>0"类过渡句（有明确后续验证或推导承接）
- 对**别人**工作的假设转述（"Smith assumed X"）
- 与研究内容无关的修辞或背景句

## 输出（stdout，恰好一个 JSON 对象，不要多余文本）

{"ok": true, "result": {"candidates": [
  {"quote": "<原句，必须逐字复制自 data.sentences 中的某一句>",
   "kind": "model | scope | numerical | prior-knowledge | other",
   "rationale": "<为什么这是值得记录的未验证前提，≤200字>",
   "confidence": "high | medium | low"}
]}}

硬规则：
1. **quote 必须逐字来自输入句子**——拼凑、改写、编造都会被框架丢弃并记为幻觉。
2. 没有合格候选就返回空列表：{"ok": true, "result": {"candidates": []}}。
3. 最多输出 12 条，按 confidence 从高到低。
4. 所有输出都是**候选**，最终由人工复核；你不需要为结论负责，只需要忠实与克制。
