# 开源轮子调研与精进建议

调研时间：2026-10-02。本文档只写**实测验证过**的结论，每条都能复核。

调研的出发点：我一直在用 PyMuPDF 裸文本 + 正则硬啃 PDF 排版（章节切分、参考文献区裁剪），
却没问过有没有现成的结构化抽取器。**这次查的主要就是这个。**

---

## 一、PDF 结构抽取：找到了一个真正的轮子

### 实测对比（同一批 20 篇 arXiv 全文，20 篇样本偏小，看趋势）

| 指标 | fitz 裸文本+正则 | pymupdf4llm |
|---|---|---|
| 识别到的标题**总数** | 85 | **296** |
| 能定位参考文献区的篇数 | **20/20** | 18/20 |
| 每篇耗时（首次） | ~0.2 s | 7.35 s |
| 每篇耗时（缓存后） | 0.08 s | 0.08 s |

`pymupdf4llm` 在导师论文①上的实际输出（**完整、层级正确、子节都在**）：

```
# Surface reconstruction algorithm using a modified Allen-Cahn equation
## Abstract
## 1. Introduction
## 2. Description of 3D reconstruction model
## 3. Numerical discretization for 3D reconstruction      ← method
## 4. Numerical experiments for 3D reconstruction        ← experiment
## 4.1. Parameter test
## 4.2. Simple models
...
## 5. Conclusions
## Acknowledgment
## References                                             ← 参考文献区直接是标题
```

### ⚠️ 一处必须更正：先前的「method 15%→65%」是**我的测量口径错了**

第一轮对比时，我用「标题块里是否出现 numerical + discret/scheme/method 这些词」
来判断有没有 method 段。pymupdf4llm 产出的标题是裸文本的 3.5 倍（含
`4.1 Parameter test`、`3.2 Time stepping` 之类子节），所以那个词块里**几乎必然**
出现这些词 —— 是**代理指标的假阳性**，不是真的切出了 method 段。

走完整管线重测（`extract_slots` 端到端，同 20 篇）后：

| 章节检出 | 裸文本 | pymupdf4llm |
|---|---|---|
| method | 3 | **3**（+0） |
| conclusion | 9 | 7（−2） |

**装了结构后端，method 检出一点没涨。**

### 真正的瓶颈在「归类」，不在「检测」

拿 `2609.21512`（FrFNO，ML 侧论文）逐个标题看：

```
标题总数 76   ->  按旧词表成功归类 2 个
#### **Abstract**        -> abstract
# **1 Introduction**      -> intro
#### 3.1 Architecture     -> (未归类)
#### 4 Training           -> (未归类)
#### 5.2 Ablation study   -> (未归类)
```

因为词表是照着**数值分析**论文写的（introduction / discretization /
numerical experiments），而 ML 论文的章节叫 Architecture / Training / Datasets /
Ablation。**标题找得再准，归不了类就等于没有。**

### 于是做了两件事，一件免费一件要依赖

**(1) 补词表 —— 纯 stdlib，不要钱，效果还更大**

给 `SECTION_PATTERNS` 补上 ML / 实验类词汇（architecture、training、optimiz*、
implementation、ablation、datasets、benchmark、case study、preliminaries…）。
同 20 篇实测：

| 章节检出 | 补词表前（裸文本） | 补词表后（裸文本） |
|---|---|---|
| **method** | **3** | **8** |
| conclusion | 9 | 10 |
| Protocol 槽命中率 | 55% | **65%** |
| Mechanism 槽命中率 | 75% | **80%** |

**这一条就把 method 检出从 3 提到 8，而且不需要装任何东西。**

**(2) 接 pymupdf4llm 为可选后端 —— 已在代码里，默认关闭**

同 20 篇、词表已补的前提下，加与不加结构后端：

| 槽位命中率 | 裸文本 | +结构后端 | Δ |
|---|---|---|---|
| Mechanism | 80% | **90%** | +10pp |
| Evidence | 75% | 80% | +5pp |
| Protocol | 65% | **70%** | +5pp |
| Gap | 85% | 90% | +5pp |
| Claim / Assumption | — | — | 0 |

**诚实结论**：结构后端是**稳定但温和**的增益（4 个槽位 +5~10pp），
主要收益在 Mechanism。而**补词表是更大且免费的那一半**。
20 篇的样本量只能看趋势，不能当精确值。

### 结论：互补，不是替代

- `pymupdf4llm` 给的是显式结构（`## 3. Numerical discretization`），不用猜；
- 我的正则在**裁参考文献区**上**反而更好**（20/20 vs 18/20）——
  arXiv 的 PDF 常常根本没有可见的 References 标题，它拿不到，
  所以裁剪逻辑**没有**交给它；
- 它慢 37 倍（首次），但结果落盘缓存后两者都是 0.08s/篇。

### 接法（已实施，默认关闭）

- `RHARNESS_PYMUPDF4LLM=1` 环境变量，或 `enable_structured_backend()`；
- 默认 **不注册**它 —— 零依赖是硬底线；
- 缓存文件名按模式分开（`*.txt.txt` / `*.md.txt`），
  否则切换后端会读到上一次的产物，开关变成假开关；
- 装不上时**静默退回**原有后端链，功能降级但不报错。

### 为什么这个差距值得修

marker 项目自己的 benchmark（用 LaTeX 源码当参考答案）：

| 方法 | 平均分 | 每页耗时 |
|---|---|---|
| naive 文本抽取 | 0.351 | 0.0015 s |
| nougat | 0.407 | 2.60 s |
| **marker** | **0.614** | 0.63 s |

我现在的做法就是 "naive" 那一档。这是外部第三方给出的、可复现的量级差。

---

## 二、文献理解/综合类：OpenScholar 与 PaperQA2

- **OpenScholar**（`akariasai/openscholar`，Apache-2.0，Nature 2026-02 发表）
  用 4500 万篇开放获取论文做检索增强，8B 开源模型在正确性上超过 GPT-4o 6.1%、
  超过 PaperQA2 5.5%。**GPT-4o 有 78~90% 的概率编造引文，OpenScholar 达到人类专家水平。**
  配套 `ScholarQABench`（2967 条专家查询）。
- **PaperQA2**（FutureHouse，Apache-2.0）科学文献 RAG，支持矛盾检测。

### 对本项目的意义（以及不该学的部分）

值得学的一条：**引文必须可验证**。OpenScholar 把「引用核查」做成了独立环节。

这正好对应本项目的一个真实短板，**已修**：原来的报告里每条精进点都说了机理、
挂了缺陷条目、给了最小验证实验，**却说不出「这个 100% 缺失是哪些论文造成的」**。
读者只能接受一个百分比，无法回去核对任何一篇 —— 对一个专门揭露
「论文没报什么」的工具，这是最要命的一处。

现在每条精进点都带**出处名单**（未报告该项的论文 id + 年份 + 标题，
只列有全文的条目，因为依据只来自看过原文的论文），并附「对照：报告了该项的」，
让读者能自己判断这个缺失是真机会，还是边界划错了。

不该学的：这两个都是「检索 + LLM 生成综述」，而本项目的核心立场是
**判定归脚本，判断归 prompt，LLM 是可选插件**。把它们当依赖引入，
等于把「可复核的确定性结论」换成「不可复核的概率性生成」——
与本项目存在的理由相反。

---

## 三、研究侧（相场数值）：四个现成轮子

| 项目 | 定位 | 对本课题的用处 |
|---|---|---|
| **JAX-PF**（GPL-3.0，arXiv 2601.06079） | JAX-FEM 上的**可微**相场，显式+隐式时间积分，含 AC / CH / 耦合 AC-CH 四个 benchmark，还有 Eshelby 包含 | **最相关**。可微化 → 隐式格式的伴随梯度 → 参数反演。导师拓扑优化线的天然工具 |
| **PhaseFieldX**（FEniCSx，2026-08 仍在更新） | 相场断裂/凝固，有 JOSS 论文 | FEM 路线的可靠实现，可当数值正确性对照 |
| **PRISMS-PF**（LGPL） | C++/deal.II 十万核并行，>10 亿自由度 | 工业级性能标杆 |
| **evoxels**（MIT） | 体素级**可微**物理，PyTorch/JAX 双后端，内置 CH 半隐式 FFT 求解器 | 体素相场的可微实现，与点云体素化路线直接对上 |

### ⚠️ 一处必须更正：「这条线一篇都没有」是**错的**，我连测错三次

第一轮我查语料标题，JAX-PF / JAX-FEM / FEniCS / PRISMS / differentiable /
inverse design **各 0 篇**，据此写下了「可微相场求解器这条线 0 篇」。
**这个结论不成立。** 三次错误各是不同形态，根子是一个：

| # | 我怎么查的 | 为什么错 | 真相 |
|---|---|---|---|
| 1 | 只搜**标题** | 标题里没有 ≠ 论文里没有 | 全文级 `differentiable` **52 篇**、`adjoint` **42 篇** |
| 2 | 全文级搜 `adjoint` 就当成可微求解器 | **术语碰撞** | 多数是**泛函分析里的伴随算子**："Duality estimates for subdiffusion"、"Rigidity and existence of Busemann profiles for the Allen–Cahn equation" |
| 3 | 搜具名框架 `prisms` | **子串误匹配** | 命中的是 "hexagonal prisms"（Wulff 形状）和 "tetrahedra, prisms, or hexahedra"（网格单元），不是 PRISMS-PF |

而同期真查出来的、被我漏掉的事实：

- **FEniCS 实际被 6 篇用了**，其中 `2511.14623` 明写
  "For the FEM baseline, we implemented a classical phase-field **topology
  optimization** solver in FEniCS" —— 就在导师的拓扑优化线上；
- PETSc 出现在 `2509.06971` 的拓扑优化工作里；
- 「可微求解器」这个方向**薄且没人给它命名立题**，但**不是空的**。

### 因此建了 `rharness/audit/topics.py` —— 并且它**不给结论**

教训不是「换个词再数一遍」，而是：**词频统计支撑不了任何结论。**
能支撑的只有一件事：把候选篇目连同**原文片段**摆出来，让人几十秒分诊完。

两个防碰撞机制：

1. **共现要求**：一个探针可以声明「必须同时命中 A 组和 B 组」。
   `adjoint` 单独出现不算数，必须同时出现 differentiable solver /
   gradient-based / inverse problem 之一 —— 这样「伴随算子」和「伴随法求梯度」就分开了。
2. **出处分离**：参考文献区预先裁掉，只引述不算。

同一批 247 篇，加共现要求前后的对比：

| 主题 | 朴素词频 | 加共现要求后 | 说明 |
|---|---|---|---|
| 可微求解器（伴随法/梯度） | 52 | **1** | 唯一 1 篇是 `2601.13293`，shape optimization + adjoint + gradient-based，确实对口 |
| 相场反问题/参数反演 | — | **22** | |
| 神经网络替代相场求解 | — | **69** | 本方向 2025–2026 最拥挤的一条 |
| 相场拓扑优化 | — | **15** | 导师的人工骨植入体方向 |
| FEniCS/有限元实现 | 11（含误匹配） | **14** | |

**诚实结论：这条线是「薄且无人命名」，不是「空白」。**
`可微求解器 1 篇` 对 `神经网络替代相场 69 篇` 这个悬殊，本身就是个值得说的观察 ——
但它是**观察**，不是「没人做过所以是机会」的立项依据。后者需要去查期刊库，
arXiv 上本来就覆盖不到计算数学方向。

### 一个反面教训：交付物里不能留已知错误的数字

上面这张表的第一版里，「method 检出 15%→65%」和「可微相场 0 篇」
都是我自己测错写进去的。两者都已在本文件里更正并写明错在哪。
一个专门揭露「论文没报什么」的工具，如果自己的报告里留着错数字，
那是自伤 —— 这条纪律对文档和对代码一视同仁。

---

## 四、动手顺序与完成情况

| # | 事项 | 状态 |
|---|---|---|
| 1 | 补章节词表（ML/实验类词汇）—— 纯 stdlib | ✅ 已做，method 检出 3→8（20 篇样本） |
| 2 | 接 pymupdf4llm 为**可选**后端 | ✅ 已做，默认关闭，4 个槽位 +5~10pp |
| 3 | 给精进点加出处引用 | ✅ 已做（借鉴 OpenScholar） |
| 4 | 给 `gaps` 加**主题空间**审计 | ✅ 已做（`rharness topics`），带共现要求 + 原文片段 |
| 5 | 真要碰可微相场，先读 JAX-PF 的四个 benchmark | ⬜ 未做（研究动作，非工程） |

第 5 条的落点：**JAX-PF**（`SuperkakaSCU/JAX-PF`，GPL-3.0，arXiv 2601.06079）
自带 AC / CH / 耦合 AC-CH 四个 benchmark，显式+隐式两套时间积分，
还有 Eshelby 包含（晶格失配）。可微化 → 隐式格式伴随梯度 → 参数反演，
这正是它在 arXiv 上给的那个 inverse design 演示所做的事。
**他们已经把「哪些格式在什么量纲下稳定」这件事踩过坑了**，值得先读。

按 `rharness topics` 的实测：语料里「可微求解器」只有 **1 篇**，
「神经网络替代相场求解」有 **69 篇**。这个悬殊值得说，但它是**观察**，
不是立项依据 —— 立项要去查期刊库，arXiv 本来就覆盖不到计算数学方向。
