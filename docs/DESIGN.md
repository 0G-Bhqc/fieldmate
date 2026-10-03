# fieldmate · 设计契约（DESIGN）

> 本文档是「挂到任意 Harness」承诺的**验收依据**：宿主集成方、MCP/Skill 适配层、
> CLI 三者共同遵守的边界。代码注释里的 H1–H6、退出码引用都以本文为准。
> 定位与路线见 [PLAN_v2](PLAN_v2.md)；红线：**不审稿**——所有判定只回答
> 「下一步研究怎么做」，不给论文打分。

---

## 1. 两个不可让步的约束

**① 不接 LLM 也能跑。** 核心零依赖（纯 stdlib），`pip install .` 后立刻可用；
LLM 是可插拔的外部依赖，不是前置条件。PDF 解析是**可选**增强（`.[pdf]`），
装不上时工具诚实报告「无法解析」，绝不静默编造。

**② 判定归脚本，判断归 Prompt。** 所有算术、比对、schema 校验、阈值判定都在
确定性代码里且有测试（150）；LLM/宿主只能产出**候选**，不能产出**判定**。
理由：Harness 里的 LLM 每次跑都可能不一样，让模型判断「差 5% 还是 50%」
就失去可复现性——可复现是科研基础设施的最低要求。

## 2. 数据与知识布局（v0.4.0 起随包分发）

| 资产 | 位置 | 说明 |
|---|---|---|
| 缺陷库（知识） | `fieldmate/libraries/defect_patterns.jsonl` | 19 条实测条目；`detection` 字段必填 |
| 检测规则（执行） | `fieldmate/contracts/detection_rules.json` | 16 条；知识/检测分离，规则可被测试 |
| 人工标注集 | `fieldmate/libraries/goldset.jsonl` | 55 条；`evaluate` 用它算精确率/召回率 |
| 目标语料 | `fieldmate/libraries/corpus.json` | 人工指定（期刊线主力），PDF 路径绝对 |
| 批量语料 | `fieldmate/libraries/corpus_*.json` | arXiv manifest（元数据+全文计数），离线可复现 |
| 检索词汇表 | `fieldmate/libraries/queries.json` | 29 条实测检索式 + 污染证据 + PDF 端点对拍 |

加载一律走 `fieldmate/_paths.py`（`importlib.resources`），wheel 与 editable 同一逻辑；
用户显式传入的 `--library/--rules/--gold/--corpus` **永远优先**于默认值。
缓存（`.fieldmate-cache/`，PDF + 解析文本）是派生数据，默认相对 cwd，
manifest 里的相对 `cache_dir` 会向上锚定到真实存在的目录（宿主任意 cwd 可用）。

## 3. 三态语义与证据分级（判定层核心）

**三态**：命中（看过且信号满足）/ 未命中（看过且不满足）/ **无法判定**（没看过）。
「未出现」只有在「确实看过」时才是信号——这条来自 D-REP-001 假阳性事故
（在空全文上求值 absent 信号 → 7/7 假阳性），已固化回归测试。

**证据强度**（`gaps`）：判据是**缺失条目里有多少解析了全文**。
STRONG = 缺失项基本都有全文却仍未提及（真·未报告的证据）；
WEAK = 只有摘要层面（摘要这种体裁不写 Δt/分辨率，无信息量）。
退出码 4 = 无 STRONG：**工具在拒绝给你一个它自己都不信的结论**。

## 4. 集成契约（H1–H6）

| # | 契约 | 宿主可以依赖什么 |
|---|---|---|
| H1 | **无状态** | 所有持久化走文件系统；进程内不保存会话；可并发调（缓存写有 `.part` 原子落盘） |
| H2 | **契约显式** | 每个子命令有 `--help`；stdout 只输出报告（JSON/Markdown/CSV），进度与警告走 stderr；退出码见 §5 |
| H3 | **可脱离 LLM** | `--llm none` 是默认（Phase 3 前是唯一模式）；全流程纯 stdlib 可跑 |
| H4 | **数据随包** | 缺陷库/规则/标注集/manifest 全在包内，wheel 安装即可用；用户路径覆盖默认值 |
| H5 | **判定归脚本** | 数值、阈值、三态、分级全部出自 CLI；宿主 LLM 只组织与解读，不改判 |
| H6 | **失败显式** | 数据源不可达、库缺失、schema 不符 → 报错给建议，不静默降级 |

宿主接法（任选其一，详细步骤见 [README](../README.md) §挂载）：

```bash
# 裸 CLI：subprocess，检查退出码
python -m fieldmate gaps --corpus <manifest> --format json

# MCP：pip install -e ".[mcp]" + python adapters/mcp/server.py（stdio，10 工具）

# Skill + 子 Agent：安装 plugins/fieldmate 插件（见 plugins/marketplace.json）
```

## 5. 退出码契约（逐命令）

退出码语义按**子命令**解释。全局语义：`0` 成功，`1` 校验失败（输入/文件/schema），
`2` 数据源失败（不可达/解析全败/包数据损坏），`3` 参数错误。

| 命令 | 码 | 含义 |
|---|---|---|
| `list` `patterns` `doctor` `evaluate` `sources` | 0 / 1 / 2 | 常规语义；`doctor` 对包数据缺失（致命项）返回 2 |
| `compare` | 0 / 1 / 2 | 无论文=1；arXiv 失败/PDF 全败=2 |
| `gaps` | **4** | 无 STRONG 级精进点——按纪律不可据此立项（不是失败，是拒绝） |
| `coverage` | **3** | 语料存在 NO_COVERAGE 技术线——别拿它当该方向的证据源 |
| `topics` `harvest` `read` | 0 / 1 / 2 | `harvest` 全被闸门拒收=4（一条没进，同类语义）；`read` 无可解析 PDF=2 |
| `prereg` | 1 | 预注册不合格（缺认输条件/必需对照） |
| `verify` | **5** | 有假设被真推翻——负结果不是命令失败，但调用方应当知道 |

## 6. 扩展规则（加一条缺陷）

1. **两边都要改**：`fieldmate/libraries/defect_patterns.jsonl` 加知识条目
   （`detection` 字段必填——无可执行检测方式的条目不准入库），
   `fieldmate/contracts/detection_rules.json` 加对应规则。
2. 自检：`python -m fieldmate patterns`（确认无欠账/孤儿规则）→
   `python -m fieldmate evaluate`（确认精确率没有塌方）。
3. 新增正则要同时写正例与**硬负例**测试（v1/v2 的教训：`stability` 一词
   在相场/ML 论文里 99% 指物理或训练稳定性）。
4. 用 `pytest` 把新条目的回归钉住；`ruff check .` 干净。

## 7. 版本与兼容

- **v0.4.0**（当前）：数据随包（H4）+ `doctor` + MCP 10 工具 + 退出码常量化；
  更名 `fieldmate`（包、CLI、环境变量 `FIELDMATE_QUERY`、缓存 `.fieldmate-cache/`）。
- v0.3.0 更名前的 `research-harness`/`rharness` 不再兼容；旧的
  `.rharness-cache/` 仍被 `.gitignore` 识别以防历史工作区误入库。
- 计划内未实现：`--llm-cmd` 适配层（协议见 `fieldmate/llm/__init__.py`），
  concept_graph，期刊画像，多源检索（OpenAlex/Crossref）。
