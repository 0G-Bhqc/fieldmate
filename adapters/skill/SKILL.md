---
name: research-harness
description: 用于计算科学研究（数值 PDE / 几何处理 / 计算成像 / 相场类方向）的文献横向对比、缺陷检查、精进点挖掘与实验核验。当需要：检索并横向对比某个研究方向的论文、找出可做的精进点、检查论文的可复现性与评测协议完整性、判断语料够不够覆盖某个方向、或把假设落成可证伪的实验并核验结果时使用。触发词：横向对比、文献对比、复现性检查、评测协议、精进点、这个方向最近有什么进展、帮我读这篇论文、arXiv 检索、我的语料够吗、验证实验结果。
---

# research-harness

## 这个 Skill 做什么

把「读文献」从**摘要式消费**变成**可复核的判定**。三项能力：

| | 能力 | 入口 | 产出 |
|---|---|---|---|
| ① | **找精进点** | `gaps` | 每条含缺失率 × 族、关联缺陷、**出处论文名单**、最小验证实验 |
| ② | **理解文献** | `compare` / `read` | 横向对比矩阵、五槽候选句、跨论文共同假设 |
| ③ | **验证实验** | `prereg` / `verify` | SUPPORTED / REFUTED / **INCONCLUSIVE** 三态判定 |

外加两个**语料自检**（这是另两项工具最容易缺、而你最需要的）：

| | 入口 | 回答什么 |
|---|---|---|
| 覆盖审计 | `coverage` | 「我关心的这些术语，语料里有支撑吗？」按**命中出处**分层（标题 / 正文 / 仅参考文献） |
| 主题审计 | `topics` | 「哪些方向有、哪些几乎没有？」每条命中附**原文片段**，供快速分诊 |

## 怎么用

```bash
# 写检索式到文件（避免 shell 拆词，见下方踩坑）
echo 'abs:"time-fractional" AND abs:"Allen-Cahn"' > q.txt

# ② 横向对比
python -m rharness compare --query-file q.txt --limit 20 --format markdown

# ① 精进点：读离线批量语料（libraries/corpus_bulk.json），不联网
python -m rharness gaps --corpus libraries/corpus_bulk.json --min-n 8

# 语料自检：先确认语料够用，再谈结论
python -m rharness coverage --corpus libraries/corpus_bulk.json   # 术语够不够
python -m rharness topics   --corpus libraries/corpus_bulk.json   # 方向分布

# ③ 实验核验
python -m rharness prereg --init
python -m rharness verify --prereg my.json --results out.json
```

依赖：核心零依赖（纯 stdlib）。`pip install -e .` 后即可用。
可选：`.[pdf]` / `.[pdfminer]` 增强 PDF 解析；`.[mcp]` 挂成 MCP server。

## 什么时候该用它

| 场景 | 怎么做 |
|---|---|
| 「这个方向最近有什么进展」 | `compare --query-file q.txt`，看方法族分布 + 年份分布 |
| 「帮我读这篇论文」 | `read --path x.pdf` 出五槽候选句，再按缺口深挖 |
| 「我能做什么」 | `gaps --corpus ...`，逐条读**出处名单**，挑一条去做最小验证实验 |
| 「我要复现这篇」 | 看「报时间步 / 报稳定条件 / 报噪声模型」三列，全是 `—` 就要预期踩坑 |
| 「我的语料够吗」 | 先 `coverage` 再 `topics`；**不要**跳过这步直接看 `gaps` |
| 「我的实验做对了吗」 | `prereg` 先注册假设，再 `verify`；INCONCLUSIVE 时不要改指标重跑 |

## 解读报告的四条纪律

1. **「未提及」≠「未做」**。这是关于**可核查性**的结论，不是关于方法质量的结论。
2. **arXiv 覆盖不全**。计算数学方向大量走期刊投稿（Langevin 方程、damping limit
   在 arXiv 实测 **0 篇**）。缺失率不能外推到全领域。
3. **区分「命中」与「无法判定」**。报告里的「另 N 项需全文」表示**没看过所以判不了**，
   不是「没问题」。
4. **`gaps` 的 STRONG 现在只表示「有全文支撑」**，不表示「方法更好」。
   真正能用来决策的是**缺失率 × 族**，不是那个强度标签。

## 常见踩坑（都实测过）

| 坑 | 症状 | 解法 |
|---|---|---|
| 检索式被 shell 拆词 | `unrecognized arguments: field AND ...` | 用 `--query-file` |
| arXiv 限流 429 | 批量下载大面积失败 | 已内置 30/60/120/240s 退避并遵从 `Retry-After`；别调小 `--interval` |
| 下载到截断的 PDF | 该论文永远读不出正文 | 已修：校验 `%%EOF` 才落 `.pdf` 名 |
| 命中数高但其实不相关 | 拿「11 篇」当支撑，核实后只有 2 篇 | 用 `topics` / `coverage` 看**命中出处**和**原文片段**，别只看计数 |
| 把「无法判定」当「未命中」 | 报告显得比实际更糟 | 看缺陷标签列的括号说明 |

## 边界（重要）

**不要**用它替代读原文。它的输出是「值得警惕的信号」，不是「结论」。
最有价值的一类缺陷（`D-REP-*`）**需要全文甚至源码才能判定**，
本工具对它们一律标为「无法判定」——这是刻意的，不是缺陷。

同理：**语料覆盖不足时，任何基于它的横向对比都不足以支撑立项。**
先跑 `coverage` / `topics` 确认语料够用，这一步不能省。

## 扩展缺陷库

判定逻辑在 `contracts/detection_rules.json`，知识在 `libraries/defect_patterns.jsonl`。
新增一条缺陷时**两者都要改**，且必须写 `detection` 字段——没有可执行检测方式的条目不准入库；
`evidence` 字段必须能被独立复核（引用论文时给原文出处）。

```bash
python -m rharness patterns    # 查看哪些缺陷还没有可执行规则
python -m rharness evaluate    # 用 gold set 体检规则的精确率/召回率
```
