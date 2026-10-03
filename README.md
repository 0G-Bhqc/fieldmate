# fieldmate

**相场/几何处理方向的科研助手**，帮研究者走完科研闭环四步：
**读文献 → 找精进点 → 定实验 → 核结果**。以 CLI / MCP / Skill+子 Agent 挂进**任意** Agent Harness。
红线：不审稿——所有判定只回答「下一步研究怎么做」。设计契约见 [docs/DESIGN.md](docs/DESIGN.md)。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![deps](https://img.shields.io/badge/dependencies-none-success.svg)](pyproject.toml)

---

## 两条硬约束

**1. 不接 LLM 也能跑。**
核心零依赖（纯 stdlib），`pip install` 后立刻可用。LLM 是**可插拔的外部依赖**，
不是前置条件——使用��不该先配好模型才能试。这是对开源项目的生死线。

**2. 判定归脚本，判断归 Prompt。**
所有算术、比对、schema 校验、阈值判定都在确定性代码里，且有测试。
LLM 只能产出**候选**，不能产出**判定**。
理由：Harness 里的 LLM 每次跑都可能不一样；如果让模型判断「这两个数差 5% 还是 50%」，
同一份代码两次评测会得出不同结论，框架就失去可复现性——而可复现是科研基础设施的最低要求。

---

## 快速开始

```bash
pip install -e ".[pdf]"     # 数据随包分发；[pdf] 装 pymupdf+pypdf 解析后端
                            # 零依赖安装：pip install . （read/--fulltext 将报「无法解析」）

# 0) 环境体检：包数据 / 编码 / PDF 后端 / 缓存（--net 探测 arXiv）
python -m fieldmate doctor

# 1) 体检缺陷库与检测规则的覆盖情况
python -m fieldmate patterns

# 2) ★ 规则体检：用人工标注集算精确率/召回率（先抓全文）
python -m fieldmate gaps --query-file q.txt --fulltext      # 顺带把 PDF 缓存下来
python -m fieldmate evaluate

# 3) 跨论文横向对比
python -m fieldmate compare --query-file q.txt --fulltext --format markdown

# 4) ★ 精进点生成（带证据强度分级）
python -m fieldmate gaps --query-file q.txt --fulltext

# 5) ★ 论文阅读卡：按阅读目的渐进披露（五槽抽取）
python -m fieldmate read --purpose beat --l2
python -m fieldmate read --purpose beat --l2 \
       --refine-assumptions --llm-cmd "<宿主 LLM 命令>"   # 判断层：隐式假设精筛

# 6) ★★ 实验预注册与核验（跑实验前后各一次）
python -m fieldmate prereg --init --out experiments/exp-001.json
python -m fieldmate prereg --prereg experiments/exp-001.json --results results.json
python -m fieldmate verify  --prereg experiments/exp-001.json --results results.json

# 7) 批量语料离线分析（248 篇，只读 PDF 缓存，不联网）
python -m fieldmate gaps --corpus libraries/corpus_bulk.json

# 8) ★ 语料自检：确认语料够用，再谈结论
python -m fieldmate coverage --corpus libraries/corpus_bulk.json   # 关键术语有没有支撑（按命中出处分层）
python -m fieldmate topics   --corpus fieldmate/libraries/corpus_bulk.json   # 哪些方向有、哪些几乎没有（附原文片段）

# 9) 体检与查表
python -m fieldmate --help                   # 所有子命令（13 个，含 doctor）
python -m fieldmate list                     # 缺陷库速览（每条是否已有可执行检测规则）
python -m fieldmate sources --path paper.pdf  # 数据源体检（arXiv 通不通 / PDF 能不能解析）
python -m fieldmate patterns                 # 缺陷库 & 规则的分栏体检（欠规则 vs 运行时缺陷）
python -m fieldmate evaluate                 # 检测规则的精确率/召回率体检
```

> **别跳过第 8 步。** 语料覆盖不足时，`gaps` 照样会产出一堆看起来很漂亮的精进点 ——
> 前提是那个语料真的覆盖了你要做的方向。实测踩过的：`adjoint` 全文命中 42 篇，
> 逐篇核实后多数是**泛函分析里的伴随算子**，真正做可微求解器的只有个位数。
> `topics` 每条命中都附原文片段，就是为了让这件事三十秒内能查清。

想直接看端到端效果（用的是本项目真实的预注册与结果）：

```bash
python -m fieldmate prereg --prereg examples/prereg_exp-pfdenoise-001.json \
                          --results examples/results_exp-pfdenoise-001.json
python -m fieldmate verify  --prereg examples/prereg_exp-pfdenoise-001.json \
                          --results examples/results_exp-pfdenoise-001.json
```

**`--fulltext` 不是可选项，是准入门禁。** 只读摘要时本框架几乎不可能给出
STRONG 级证据：arXiv 摘要是 150~250 词，**没人会在摘要里写 Δt、ε、网格分辨率**。
实测对照（同一检索式，7 篇）：

| 运行方式 | 候选精进点 | 其中 STRONG |
|---|---|---|
| 仅摘要 | 6 条 | **0** |
| 抓全文后（5/7 成功） | **3 条** | 0 |

消失的 3 条（「报分辨率」「报稳定条件」等）**是摘要层级的假信号**——
论文里其实都写了，只是不写进摘要。工具用「体裁敏感度 + 缺失条目中全文占比」
把这类假信号自动过滤掉了。

**为什么推荐 `--query-file` 而不是 `--query`：**
arXiv 检索式天然含空格与引号，而 shell、subprocess、各家 harness 的参数传递处理方式**都不一样**
（PowerShell 会直接拆词）。我实测踩过：`--query 'all:"phase field" AND all:denoising'`
在 PowerShell 下被拆成 4 个参数，直接报 `unrecognized arguments`。
所以框架提供了三条旁路：`--query` / `--query-file` / 环境变量 `FIELDMATE_QUERY`。
**这类「看起来是数据问题、实际是引用问题」的失败最难查**，所以必须内置旁路。

---

## 它做什么

给定一个研究方向，产出一份可直接贴进论文的对比报告：

```
## 横向对比矩阵
> 检索式：...  来源：arxiv  覆盖：7 篇
> ⚠ 全部 7 篇仅有标题/摘要，未取全文。下表的「已提及」只代表标题/摘要层面。

| 年份 | 方法族 | 刊物 | 报分辨率 | 报噪声模型 | 报时间步 | 报稳定条件 | 保体积/守恒 | 开源自复现 | 缺陷标签 |
|---|---|---|---|---|---|---|---|---|---|
| 2026 | 相场/Allen-Cahn | cs.CE | — | — | — | — | — | — | - (另 2 项需全文) |
| 2026 | 相场/Allen-Cahn | math.GM | — | — | — | ✓ | — | — | - (另 2 项需全文) |
...

### 关键报告项在【标题/摘要】层面的提及率
| 报分辨率 | 1 | 7 | 86% | `D-EVA-001` |
| 报时间步 | 0 | 7 | 100% | `D-RES-001` |
...

### 读这份报告的四个限制（务必一并引用）
1. arXiv 覆盖不全
2. 只看了标题/摘要 → 真·缺失率一定高于表里的数字
3. 「未提及」≠「未做」——这是关于可核查性的结论，不是关于方法质量的结论
4. 正则信号有假阳性也有假阴性，规则精度尚未被标定
```

### 三态语义（这是框架里最重要的一个设计）

缺陷检测的结果**不是**二元的「有缺陷/没缺陷」，而是三态：

| 状态 | 含义 | 何时出现 |
|---|---|---|
| **命中** | 确认存在该缺陷 | 看过，且信号满足 |
| **未命中** | 看过，且不满足 | 看过 |
| **无法判定** | **没看过，所以判不了** | 规则需要全文，但只有摘要 |

> 这条是框架在开发期**自己踩出来的**。第一版把「需要全文」的规则
> 在空的 `fulltext` 上求值，于是 `absent` 信号被判为「满足」，
> 导致 `D-REP-001`（静默兜底，一个**纯代码缺陷**）在 7/7 篇上全部**假阳性命中**。
>
> 正确语义是：**「未出现」只有在「确实看过」时才是信号。**
> 这条本身就是缺陷库 `D-REP-001` 教训的直接应用——
> 框架的第一个 bug，恰好是它自己第一条 engineering 缺陷的复现。

### 精进点生成（`gaps` 子命令）—— 能力①的实际输出

横向对比只回答「哪里有缺失」，**研究要回答的是「所以我能做什么」**。
`gaps` 把这一步显式化，并且**拒绝输出没有证据支撑的精进点**：

```markdown
### 1. 🟡 保体积/守恒
**观察**：2/7 篇提及，缺失率 71%（体裁敏感度 `medium`）
**证据基础**：缺失的 5 篇里，3 篇**已解析全文**（60%）
**为什么是问题**：去噪类方法的核心卖点常是保体积…不报漂移量时无法区分
  「保体积」与「恰好看起来还行」。
**最小验证实验**：比较保体积类（CH、AC+保真）与收缩类（Laplacian、bilateral）
  的体积漂移与表面误差，绘制精度-体积帕累托前沿。
**这条精进点的自身局限**：
- arXiv 覆盖不全…
- ⚠ 缺失的条目里只有 60% 有全文；其余仅凭摘要判定…
```

**证据强度分级**（`STRONG` / `MEDIUM` / `WEAK`）是本模块最重要的部分。
判据是**缺失的那些条目里有多少解析了全文**——有一篇论文的全文却仍未提及，
才是「真·未报告」的证据；只有摘要而摘要没写，几乎没有信息量。

退出码约定：`0`=有 STRONG 级候选；**`4`=无 STRONG，按纪律不可据此立项**。
这不是失败，这是工具在拒绝给你一个它自己都不信的结论。

---

## ⚠️ 头号发现：arXiv 检索对计算数学方向基本无效

2026-10-02 实测 7 种检索式，人工判读每种命中的论文标题：

| 检索式 | total | 人工判读纯度 |
|---|---|---|
| `all:"phase field" AND all:denoising` | 7 | **0 / 7** |
| `abs:"Allen-Cahn" AND abs:denoising` | 2 | 0 / 2（都是分数阶拉普拉斯正则） |
| `abs:"phase field" AND abs:"mesh denoising"` | 0 | — |
| `ti:"phase field" AND abs:denoising` | 1 | 0 / 1（裂纹增长预测） |
| `ti:"phase-field" AND abs:"surface reconstruction"` | 0 | — |
| `cat:math.NA AND abs:"Allen-Cahn" AND abs:denoising` | 1 | 0 / 1 |
| `abs:"Cahn-Hilliard" AND abs:denoising` | 0 | — |

**根因：`denoising` 一词在现代机器学习里专指「去噪扩散」（denoising diffusion）**，
所以任何含 `denoising` 的检索都会把整个扩散模型文献拉进来——命中的 7 篇分别是
裂纹增长预测、微结构生成扩散、非线性抛物系统、神经算子、显微图去噪、沸腾流温度场、光栅条纹滤波。

**这个方向的论文普遍走期刊投稿、不上 arXiv。** 工作区里那两篇相场论文
（PhaseFieldSurfaceReconstruction / D3SurfaceDenosing5）就是如此——arXiv 上根本没有。

**因此本工具的语料来源被设计成双轨**：
- `libraries/corpus.json` —— **人工指定的目标论文**（计算数学方向的主力来源）
- arXiv 检索 —— 仅适用于 CS/ML 类主题，或作为「顺手看看」的补充

> 这条发现也解释了为什么 gold set 一开始做得很差：我用 5 篇被污染的 arXiv 论文
> 调规则，等于在错的样本上做精细统计。**语料错了，分数再高也没用。**

### `libraries/queries.json`：本方向的真实检索词汇表

不换词，换的是**词汇**。用 PDE 社区的专属词（不会被扩散模型文献占用）实测：

| 检索式 | total | 纯度 | 说明 |
|---|---|---|---|
| `abs:"time-fractional" AND abs:"Allen-Cahn"` | 28 | **high** | 几乎全在 math.NA/math-ph，**当前最佳语料来源** |
| `abs:"reaction-diffusion" AND abs:"Lengyel-Epstein"` | 4 | **high** | 量小但全部同线（导师近两年反复用的模型） |
| `abs:"Cahn-Hilliard" AND abs:"image segmentation"` | 2 | **high** | 对应组里 TIP/TPAMI 那条线 |
| `abs:"Allen-Cahn" AND abs:"regularization"` | 138 | medium | 混杂大量 PINN / 神算算子 |
| `abs:"surface reconstruction" AND abs:"point cloud"` | 235 | medium | 混杂 CV/机器人 |
| `abs:"Gray-Scott"` | 107 | low-med | 大量 PINN 论文 |
| `abs:"Langevin equation" AND abs:"Allen-Cahn"` | **0** | — | ⚠ 导师的核心工具，但**arXiv 一篇都没有** |
| `abs:"damping limit" AND abs:"Sine-Gordon"` | **0** | — | ⚠ 同样是纯期刊线 |

**结论**：arXiv 对本方向有效的子领域是**时间分数阶 PDE（math.NA）**与**反应扩散图案形成**；
相场曲面重建/网格去噪这条主线几乎只存在于期刊，必须靠 `corpus.json` 人工指定。

**据此抓取的语料：** 13 篇（12 篇全文可解析），全部与导师研究相关。

### ⚠️ 抓取侧的教训：拿不到 ≠ 不存在

批量补全文时，大量条目被判成「下载失败」。直觉上这会被读成「arXiv 上没有这些 PDF」，
但对 3 个代表性 id 做全量对拍后发现，**那是我们用错了地址**：

| 地址写法 | `2509.07862` | `2501.01234` | `1704.02348` | 命中 |
|---|---|---|---|---|
| `http://export.arxiv.org/pdf/{base}` | 200 | 200 | 200 | **3/3** |
| `https://arxiv.org/pdf/{base}` | 406 | 200 | IncompleteRead | 1/3 |
| `https://arxiv.org/pdf/{base}.pdf` | 406 | 406 | 406 | **0/3** |

两条结论都和直觉相反：

1. **决定成败的是主机，不是 User-Agent。** 同一地址换三种 UA
   （浏览器型 / fieldmate 型 / 带 Referer）结果逐条一致，全 406 或全 200。
   代码注释里原先那句「PDF 请求改用浏览器 UA 更稳」是错的，已更正。
2. **带 `.pdf` 后缀的写法是最差的（0/3），却是大量示例代码的默认写法。**
   早期把它排在候选第一位，等于每篇先白烧一轮重试：实测单篇 **64.4 秒且完全失败**。
   改用 export 主机后，同一篇变成 **1.1 秒且成功**，请求数也从最多 12 次/篇降到 1 次/篇。

**为什么值得单列一节**：如果不查这一下，这些论文会被永久记成「该领域没有相关工作」，
一个抓取侧的 bug 就被写成了领域结论。统计里把「限流」「地址错」「条目确实没有」
混成一栏，是这类工具最隐蔽的失效方式。对拍数据落在 `libraries/queries.json`
的 `pdf_endpoints` 字段，回归测试见 `tests/test_net.py`。

代码里**没有**把 export 主机写死成唯一选项，而是保留「记住最近成功写法」的机制——
不同网络环境下可达性可能反过来，实测结论不该被当成永恒真理。

### 批量语料：247 篇（240 篇全文）

用 `libraries/queries.json` 里验证过的检索词汇表，配上导师主页 33 篇论文的关键词
（时间分数阶 Allen–Cahn / Gray–Scott / Lengyel–Epstein / Langevin 方程 / damping limit /
相场拓扑优化 / Voronoi 晶格 …）扩展到 30 条检索式，去重后得 **247 篇**：

```bash
# 装载 manifest 语料做离线分析（只读 PDF 缓存，不联网）
python -m fieldmate gaps --corpus fieldmate/libraries/corpus_bulk.json --min-n 10
```

`--corpus` 与 `--query` 是**并集**关系而非替代：检索结果通常只有摘要，
缓存语料才有全文，两者合并才是完整的证据面。
全文覆盖率不足时会自动打印警告，并在每条精进点上标注
「缺失条目里只有 X% 有全文」——证据强度该打折的时候必须打折。

**最终规模：247 篇，其中 240 篇全文可解析（97.2%）**，
全文中位长度 6.2 万字符，PDF 缓存约 1 GB（已 gitignore）。

### ★ 语料扩充最直接的回报：证据强度整体跃迁

同一套规则、同一套缺陷库，只换了语料规模：

| 语料 | 全文覆盖 | 候选 | **STRONG** | WEAK |
|---|---|---|---|---|
| 早期 14 篇（2 期刊 + 12 arXiv） | 12 篇 | 6 | 0 | 6 |
| 中途 62 篇有全文 | 62/247 (25%) | 30 | **0** | 20 |
| **扩充后 247 篇** | **240/247 (97%)** | 22 | **22** | **0** |

**这直接验证了本框架的核心论断**：只读摘要时几乎产不出 STRONG 级证据，
因为 arXiv 摘要是 150~250 词，**没人会在摘要里写 Δt、ε、网格分辨率**。
全文覆盖率从 25% 提到 97%，22 条候选**全部**从 WEAK 升到 STRONG。

按缺陷条目合并后的统计（同一缺陷在不同子族各出一份，取最高缺失率）：

| 条目 | 子族数 | 报告 | 总数 | 最高缺失率 | 缺失项的全文支撑 |
|---|---|---|---|---|---|
| 开源自复现 | 5 | 10 | 241 | **100%** | 224/231 |
| 保体积/守恒 | 4 | 45 | 229 | 93% | 178/184 |
| 报分辨率 | 4 | 66 | 226 | 88% | 153/160 |
| 报稳定条件 | 5 | 60 | 241 | 87% | 174/181 |
| 报噪声模型 | 3 | 70 | 214 | 68% | 138/144 |
| 报时间步 | 1 | 51 | 123 | 58% | 68/72 |

最后一列是关键：**缺失判定里有 96~97% 是基于已解析全文做出的**，
所以「没提到」是全文层面的判断，而不是「摘要这种体裁不写它」。

「报噪声模型」缺失率 68% 这一条，与本项目 `pfdenoise` 的实测结论互为印证：
噪声模型在去噪文献里长期不是一等公民。

### ⚠️ 扩语料后，规则体检的分数**变差了**

| 报告项 | 2 篇时 | **8 篇 55 条时** | 变化原因 |
|---|---|---|---|
| 报分辨率 | **1.00** | 0.71 | 新语料是 math.NA 数值分析论文，暴露了新的假阳性模式 |
| 报噪声模型 | 0.50 | 0.40 | 同上 |
| 报稳定条件 | 0.00 | 0.50 | 真实数值格式论文确实报稳定条件，规则终于能抓到真的 |
| 开源自复现 | 1.00 | 1.00 | 稳定 |
| 全部项召回率 | 1.00 | **1.00** | **零假阴性** |

**这不是退步，是测量精度提高了。** 2 篇样本时那些 1.00 里有运气成分；
样本翻 4 倍后，「这个规则到底靠不靠谱」才第一次有了可信的回答。
其中 `spatial discretiz\w+` 就是在新语料上暴露的典型假阳性——
它匹配的是「我们用中心差分做空间离散」这类**方法描述**，不是分辨率报告，已删除。

**结论仍然是：这些规则的精确率在 0.4~1.0 之间，样本量仍不足以支撑统计结论。**
它们适合当**分诊线索**，不适合当判据。

---

## 能力②　五槽抽取 + 渐进式披露阅读卡（`read`）

```bash
python -m fieldmate read --purpose beat --l2      # 超越某篇该看什么
python -m fieldmate read --purpose implement     # 复现某篇该看什么
```

**要解决的问题：对所有论文都给同样的均匀深度，是 LLM 论文助手最大的浪费。**
你从一篇论文里需要什么，取决于你读它干什么：

| 目的 | 展开的槽 |
|---|---|
| `implement` 复现 | Mechanism, Protocol |
| `beat` 超越 | **Assumption, Gap**, Protocol |
| `cite` 引用 | Claim |
| `build-on` 承接 | Assumption, Gap |

默认只给 L1 一屏（Claim + 各槽命中数 + 最优先的 Assumption/Gap 句），`--l2` 才展开原句。

### 实测（两篇真实相场论文）

`Protocol` 槽准确抓到了：
- `the uniform mesh size is defined as h = (b−a)/Nx`
- `time step is ∆t = T/Nt`
- `For the boundary values, we apply the homogeneous Neumann boundary condition`

**这正是复现一篇数值论文真正需要的东西**，而它在摘要里一个字都没有。

### 这个组件是全包最弱的一环（必须说清）

| 槽 | 可靠性 | 说明 |
|---|---|---|
| Protocol | ✅ 好 | 有明确的句法标记（`h =`、`∆t =`、边界条件），实测准确 |
| Claim | ✅ 可用 | `we propose/present` 之类标记稳定 |
| Evidence | ⚠ 一般 | 与 Protocol 共用标记，区分度低 |
| Mechanism | ⚠ 一般 | 依赖 "is given by / we solve" 类表达 |
| **Assumption** | **❌ 弱** | 两篇真实论文**均未命中** |
| **Gap** | **⚠ 有假阳性** | 抓到 "However, the complexity of…" 这类转折，但不是真局限 |
| 章节切分 | **❌ 不可靠** | 见下 |

**章节切分在三篇真实 PDF 上摆了三次**：过松（`method` 命中 17 次）→ 过严（`method`/`experiment` 整段消失）→ 最终加了**退化保护**。
结论是：**正则不可能对所有期刊的排版成立**。所以正确做法不是继续调参，而是
「切分覆盖不到全文 30% 就退回全文扫描」——槽位匹配只依赖句法标记，本来就不需要章节。
实测退回全文后 Protocol 反而抓得更准。已固化为 `test_slots_falls_back_to_fulltext_when_sections_unreliable`。

**Assumption 槽为什么弱**：假设句在实证类论文里密度高但**很少被验证**，可是
「we assume the material is homogeneous」和「we assume readers know PDEs」的学术价值天差地别，
正则判不了。这正是**判定归脚本、判断归人**的边界所在，也是 Phase 2 的 LLM 抽取最该接手的地方。

> 因此本模块的定位是**分诊**，不是结论。所有输出都是候选句，必须人读。

---

## 能力③　实验预注册与结果核验（`prereg` / `verify`）

```bash
python -m fieldmate prereg --init --out experiments/exp-001.json        # 生成模板
python -m fieldmate prereg --prereg experiments/exp-001.json \
                          --results results.json                       # 校验（不合格就别开跑）
python -m fieldmate verify  --prereg experiments/exp-001.json \
                          --results results.json                       # 三态核验
```

**要解决的问题：「代码跑通了」和「验证了主张」之间的鸿沟。**
最贵的失败不是程序崩溃，而是**跑完了、看着结果、然后改指标让它看起来对**。

### 三态判定（第三种最容易被糊弄）

| 判定 | 含义 |
|---|---|
| **SUPPORTED** | 预注册方向成立，且无已知混淆因素解释 |
| **REFUTED** | 方向不成立，**且无混淆因素能解释** → 主张被推翻，如实写进论文 |
| **INCONCLUSIVE** | 方向不成立，**但存在已知混淆因素** → 当前实验**无法区分**「方法更差」与「设置不足」 |
| UNTESTED | 结果里缺这个指标/方法。**缺结果不是否定证据** |

### 真实案例：这个工具抓出了我自己的一次误读

`examples/` 里放了一对真实文件——预注册写于实验之前，结果是当天实际跑出来的那张表：

| # | 假设 | 判定 | 实测 |
|---|---|---|---|
| H1 | `\|radius_drift(pf)\| < \|radius_drift(lap)\|` | ❌ REFUTED | 0.05 vs 0.03，**拉普拉斯反而更小** |
| H2 | 相场在 CD 上不劣于拉普拉斯 | ❌ REFUTED | 0.0807 vs 0.016，误差是 res_floor 的 3.0 倍，**不是分辨率噪声** |

**这纠正了我此前的一个说法。** 我之前说「相场的优势在保体积（pf +0.05% vs 拉普拉斯 −0.03%）」——
可绝对值是 0.05 > 0.03。6000 点这个密度下**所有**方法的体积漂移都在 0.1% 以内，
体积根本不具区分度；真正保体积的是 `cahn_hilliard`（+0.25%），收缩最狠的是 `pf_ac`（−0.97%）。

工具的诊断：假设写 `|a| < |b|` 但指标给的是带符号值时，**必须按绝对值比**——
否则拿 +0.05 和 −0.03 比大小，结论直接反掉。已固化为回归测试
（`test_verify_compares_absolute_values_when_statement_uses_bars`）。

### 三条硬规则

1. **认输条件必须机检**：`falsification` 字段要含明确措辞（「被推翻 / 不成立 / 证伪」…），
   否则视为「不是可证伪的主张」而拒收。
2. **必要对照自动推导**：主张涉及精度比较却没有 `res_floor` 对照 → 直接判不合格。
   另有 `identity_baseline`（恒等变换基线）、`noise_model`（噪声模型声明）。
3. **事后补注册检测**：结果文件时间早于预注册时间 → 标记 `backfilled` 并告警。
   审稿人一眼能看出来。

退出码：`0` 合格/无推翻　`1` 预注册不合格　`5` **有假设被真推翻**（负结果不是命令失败，但调用方应当知道）

### 负结果也是资产

`verify` 不会因为结论不利于预期就吞掉结果。REFUTED 时的建议固定是：
「如实写入论文的负结果，先对照缺陷库 D-REP-* 检查实现，确认无误后再下结论；
**不要改指标或换基线**。」

---

## 规则体检（`evaluate` 子命令）—— 工具对自己的诚实体检

```bash
python -m fieldmate evaluate          # 用人工标注 gold set 算精确率/召回率
```

gold set 共 **55** 条 = 目标领域论文 12 条（作者亲读全文、高置信度）+ 其他论文 43 条。

| 报告项 | 精确率 | 召回率 | 目标域样本 | 判定 |
|---|---|---|---|---|
| 报分辨率 | **1.00** | 1.00 | 2 | ✅ 可用 |
| 开源自复现 | **1.00** | 1.00 | 2 | ✅ 可用 |
| 报时间步 | 0.75 | 1.00 | 2 | ⚠ 需收紧 |
| 保体积/守恒 | 0.67 | 1.00 | 2 | ⚠ 需收紧 |
| 报噪声模型 | 0.50 | 1.00 | 2 | ⚠ 需收紧 |
| 报稳定条件 | 0.00 | 1.00 | 2 | ❌ 不可用 |

**所有剩余假阳性都在 arXiv 那批污染语料上**——再次印证语料纯度是主要矛盾。

### 正则演进史（请勿回退）

| 版本 | 做法 | 结果 |
|---|---|---|
| v1 | 直接搜关键词 | 报稳定条件 **0.00**、报分辨率 0.25、报时间步 0.25 |
| v2 | 要求「数值格式语境词」 | 假阳性清零，但**在真实相场论文上全部假阴性** |
| v3 | 按真实论文书写习惯补正例形式 | 召回率回到 1.00 |

v2 的教训最值钱：**收紧过头和放宽过头一样是 bug**。
真实论文写的是 `N = Nx = Ny = Nz = 100` 和 `h = (b−a)/Nx`——
既没有单位、也没有 `N×N×N` 的乘号，v2 的模式全部漏掉。
另外 `Δt` 的希腊字母 Δ(U+0394) 与增量 ∆(U+2206) **两种都在真实论文里出现**，
字符类少写一个就漏检。

v1 的教训同样值钱：`stability` 在相场/ML/材料论文里绝大多数指**物理或训练稳定性**
（「austenite becomes more stable」「training stability」「long-horizon stability」），
不是格式的数值稳定性。

**硬负例已固化为测试**（`test_report_item_regexes_reject_contextual_false_positives`），
外加 `test_all_report_item_regexes_compile`（真的因为多留一个右括号让整个包 SyntaxError 过）。

### 这份体检自身的局限

1. **样本量极小**：每项 5~7 个样本，精确率一格变动就是 14~20%。只用于**发现明显失效**，不是度量。
2. **标注者单一**：只有我一个人判读，存在系统性偏见。
3. **假阴性比假阳性更难发现**：v2 在污染语料上分数漂亮，却在真实论文上全军覆没。
4. 目标语料目前只有 2 篇，**不足以支撑任何统计结论**。

---

`fieldmate/libraries/defect_patterns.jsonl` —— **19 条，全部是实测验证过的真实数据**，不是示例。

| class | 条数 | 代表 |
|---|---|---|
| `reproducibility` | 5 | 论文 λ 符号反了、Δt 量纲抄错、边缘函数记号歧义 |
| `evaluation` | 5 | 未报分辨率下限、N2N 一致性被恒等变换奖励、噪声分布不可迁移、显式/隐式对比不对齐精度 |
| `engineering` | 4 | 静默兜底、近邻偏移错向、亚格点量纲错、Hausdorff 单向索引 |
| `modeling` | 2 | 纯 AC 腐蚀液滴、目标场需 ±1 而非 0/1 |
| `discretization` | 2 | 时间步量纲、CH 四阶刚性 |
| `differentiable` | 1 | 可微相场里轨迹长度是独立于算力的约束（逆向 AD 存整条轨迹） |

`verified` 15 条 / `reported` 4 条；`detection` 与 `evidence` 字段零缺失。

**知识（是什么）与检测（怎么找）分离：**
- `fieldmate/libraries/defect_patterns.jsonl` —— 知识，可被引用与讨论
- `fieldmate/contracts/detection_rules.json` —— 机器可执行的检测规则，可被测试与演进

> **入库硬标准：没有可执行检测方式的条目不准入库。**
> 只写「注意事项」的条目无法被复用，只会变成又一篇需要人读的文档——
> 那样的话缺陷库就退化成了没人读的长文档（R1 风险）。
> `python -m fieldmate patterns` 会明确列出哪些缺陷还没有可执行规则。

---

---

## 自动语料构建（`harvest`）

```bash
# 默认只用验证过的高纯度检索式
python -m fieldmate harvest --per-query 10 --only high

# 把被污染的检索式也放进去，验证闸门到底拦不拦得住
python -m fieldmate harvest --per-query 8 --only all --no-download

# 消融实验：关掉词法闸门，看纯学科闸门值多少
python -m fieldmate harvest --only all --no-term-gate --no-download
```

**这个子命令的价值不在「抓」，在「抓完敢扔」。**
不加闸门的话，语料里一半是扩散模型论文，gold set 和规则评估会一起被带偏。

### 两道闸门

| 闸门 | 判据 |
|---|---|
| **A 学科门** | arXiv 主分类须落在 math.NA/AP/DS/PR/SP、math-ph、cond-mat.mtrl-sci、cs.CE/NA/CV、eess.IV。`cs.LG`/`cs.AI`/`cs.CL` 且无数学分类 → 直接拒 |
| **B 词法门** | 必须命中**强术语**；仅命中弱术语时，要求「有核心数学分类 + 无 ML 特征词」 |

### 术语强弱分档 —— 这是一个真实的修正

首版把 `phase[- ]field` 当强术语，实测在污染语料上放进 **4 篇全不相关的**：
扩散模型微结构生成、非线性抛物系统理论、深度学习显微图去噪、CNN 光栅条纹滤波。

根因：**「phase field」在 ML/物理论文里也指「系统的相（序参量）场」**，与「相场方法」两回事。
改成**必须搭配方法论排他性的写法**才算强术语（`phase-field method/model/simulation`、
`Allen-Cahn`、`Cahn-Hilliard`、`Ginzburg-Landau`、`Langevin equation`、
`Gray-Scott`、`Lengyel-Epstein`、`time-fractional` 等 24 条）。

### 闸门效果（实测）

| 检索式 | 抓取 | 首版收录 | **强弱分档后** |
|---|---|---|---|
| `time-fractional AND Allen-Cahn` | 8 | 8 | **8** |
| `reaction-diffusion AND Lengyel-Epstein` | 4 | 4 | **4** |
| `Allen-Cahn AND regularization` | 8 | 1 | 1（拒 7 篇 PINN/神经算子） |
| `Gray-Scott` | 8 | 1 | **0**（拒 7 篇 PINN） |
| `all:"phase field" AND all:denoising`（污染） | 7 | 4 | **3**（拒 4） |
| **合计** | 45 | 27 | **25**（拒收率 42%） |

### 闸门还做不到什么（必须说清）

`all:"phase field" AND all:denoising` 仍放行的 3 篇（微结构扩散模型、非线性抛物系统、显微图去噪）
**确实都真的在用相场建模**，只是应用子领域不同。

**所以闸门是「主题过滤」，不是「子领域过滤」。** 区分「相场方法 + 去噪」和
「相场方法 + 微结构生成」需要读摘要的**应用对象**——这正是 LLM 该干的活，
也是框架「判定归脚本、判断归 prompt」那条边界所在。闸门只负责挡掉明显的错。

被拒的每篇都记账在 `rejection log` 里（谁被拒、哪道门、什么理由），
方便你随时检查闸门是不是误杀了好东西。

---

核心是 CLI，**任何 harness 都能挂**：

| 形态 | 做法 |
|---|---|
| 裸 CLI | `python -m fieldmate gaps --query-file q.txt --fulltext --format json` |
| 任意 Python harness | `import fieldmate; fieldmate.mine_gaps(rows, stats, library)` |
| MCP | `pip install -e ".[mcp]"` + `adapters/mcp/server.py`（10 个工具，三项能力各有入口） |
| **Skill + 子 Agent（推荐）** | `plugins/fieldmate/` 插件源 + `plugins/marketplace.json` 本地市场，装进宿主即自动注册（四步闭环剧本 + 同名子 Agent） |
| Agent Skill | `adapters/skill/SKILL.md`，抄走即可 |

集成契约见 [docs/DESIGN.md](docs/DESIGN.md) 的 §4（H1–H6）。

### 退出码（语义按子命令解释，完整表见 [docs/DESIGN.md](docs/DESIGN.md) §5）

| 码 | 含义 |
|---|---|
| 0 | 成功（`gaps` 且有 STRONG 级候选） |
| 1 | 校验失败（没取到论文、schema 不符、预注册不合格） |
| 2 | 数据源失败（arXiv 不可达、PDF 解析全失败、包数据损坏） |
| 3 | 参数错误；**`coverage` 下 = 语料存在未覆盖技术线** |
| **4** | **`gaps`：无 STRONG 级精进点；`harvest` 全被闸门拒收（一条没进）** |
| **5** | **`verify`：有假设被真推翻——负结果不是命令失败，但调用方应当知道** |

---

## 现状与边界

**Phase 1 已完成并实测：**
- arXiv 检索（限速、分页、去重、query 落盘）
- arXiv 全文抓取（PDF 缓存、`IncompleteRead` 容忍、**406 主机绕行**、fitz/pypdf/pdfminer 自动降级）
- **429/503 专用指数退避**（30/60/120/240 秒，尊重 `Retry-After`）——通用重试在限流上完全无效
- **manifest 语料装载**（`load_manifest` / `gaps --corpus`）：只读不下载，离线可复现
- **解析结果磁盘缓存**：按 (size, mtime, max_chars) 失效，实测 240 篇 214.6s → **18.9s**
- **目标语料清单**（`corpus.json`，人工指定本地 PDF）—— 计算数学方向的主力来源
- **批量语料**（`corpus_bulk.json`）：30 条检索式 × 248 篇去重（fitz 实测 246 篇全文）
- 缺陷库加载 + 16 条可执行检测规则
- 横向对比矩阵 + 缺失统计（Markdown / JSON / CSV）
- 精进点生成 + 证据强度分级（STRONG / MEDIUM / WEAK）
- **规则体检**（`evaluate`）：人工标注 gold set 55 条 / 8 篇论文，精确率/召回率 + 失效案例
- **五槽抽取 + 阅读卡**（`read`）：按阅读目的渐进披露
- **检索词汇表**（`libraries/queries.json`）：本方向实测有效的检索式 + 污染证据 + PDF 端点对拍
- **自动语料构建**（`harvest`）：两道相关性闸门 + rejection log（可另接 `--llm-cmd` 子领域闸门 C）
- **判断层**（v0.5.0，Phase 3）：`--llm-cmd` 适配层 + `read --refine-assumptions` 双路精筛
  （句级 + 隐式假设 chunk-scan）+ 幻觉闸门（quote 逐字回对原文）
- 169 个测试（每个对应一次真实踩坑）+ wheel 冒烟测试（干净安装验收）
- **数据随包分发**（v0.4.0）：pip install 后立即可用，不再依赖仓库布局
- **doctor 子命令**：环境体检第一入口；MCP 10 工具；Skill+子 Agent 插件源

**判断层已落地（Phase 3）**：`--llm-cmd` 接宿主任意 LLM 命令（stdin/stdout JSON 协议，
不接时纯脚本照样可跑）。两个判断任务：`read --refine-assumptions`（Assumption
槽双路精筛：句级判断 + 无标记文本的隐式假设分块扫描）与 `harvest --llm-cmd`（子领域
过滤，判断进 rejection log 可复核）。**幻觉闸门**：LLM 的 quote 必须逐字命中原文，
否则丢弃记账——判定永远在脚本里。

**尚未做（Phase 1/4）：** concept_graph、期刊画像、多源检索（OpenAlex/Crossref）、负结果自动回写缺陷库。

**已知局限（不要绕过它们引用本工具的结论）：**
1. **arXiv 对计算数学方向基本无效**（见上节实测表）——目标语料靠 `corpus.json` 人工指定
2. arXiv 覆盖不全；只看摘要时缺失率被**低估**，需 `--fulltext` 缓解
3. 「未提及」≠「未做」
4. **正则精度 0.4~1.0**，样本量 5~9/项，**不是可信度量**，只适合当分诊线索
5. **bulk 语料未做相关性人工判读**。247 篇是按 30 条检索式机械去重得来的，
   其中必然混入「词面沾边但不在做同一件事」的论文——`harvest` 的两道闸门**没有**
   施加到这批语料上（那是手工抓取路径）。所以 bulk 语料只适合做
   「这个领域在关心哪些报告项」的粗筛，**不能直接用来断言某缺陷的普遍缺失率**。
6. 规则体检的 gold set 仍只覆盖 8 篇论文，**尚未随 bulk 语料扩充**。
   上面那张缺失率表**没有经过精确率体检**——它用的是同一批尚未验证精度的正则规则。
   引用具体数字前请先跑 `evaluate` 看规则的精确率。
7. bulk 语料按 `submittedDate` 降序抓取，**年份严重偏斜**（2026 年 108 篇 / 2025 年 42 篇，
   2000~2015 合计仅 13 篇）。用它来谈「这个领域的历史」会得出错误结论。

---

## 引用

用了本工具的结论，请一并引用被统计的原始工作，以及方法学依据：

```bibtex
@article{pointclouddenoise-survey2025, title={A Survey of Deep Learning-based Point Cloud Denoising}, journal={arXiv:2508.17011}, year={2025} }
@article{noise2noise-revisited2026, title={Noise2Noise Revisited: Training Pair Distributions Dominate Loss Choice}, journal={arXiv:2609.16788}, year={2026} }
@book{knoll2001reproducibility, title={Reproducibility of Scientific Investigations}, author={Knoll, George}, year={2001} }
```

## 贡献

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

最需要的贡献：**给检测规则补人工标注集，算出每条规则的精确率/召回率**——
现在所有规则的精度都是未知的，这是本工具最该被质疑的地方。

## License

MIT，见 [LICENSE](LICENSE)。
