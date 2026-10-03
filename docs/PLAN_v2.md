# fieldmate · 定位与路线图（定稿）

> v2 定稿（2026-10-02）。已拍板三项决策，本文不再是草案：
> ① 更名 **fieldmate**（fieldmate → fieldmate，PyPI 已核实可用）；
> ② 领域范围**只做 L1**（相场几何处理），金融线不纳入，L2/L3 进暂缓池；
> ③ 交付形态**以 Harness 内 skill / 子 Agent 为主**，MCP 次之，裸 CLI 是底座。
>
> 三能力架构主张沿用 docs/PLAN.md（v0.1）。上一版把叙事推到「裁判/审稿」是偏的，
> 本版已修正为「科研助手」，并把这条教训写进红线（§5）。

---

## 0. 定位一句话

**fieldmate 是相场/几何处理方向的科研助手，以 skill 或子 Agent 形态挂进任意 Harness，
帮研究者走完科研闭环的四步：读文献 → 找精进点 → 定实验 → 核结果。**

| 四步 | 子命令 | 回答的问题 |
|---|---|---|
| 读文献 | `compare` / `read` | 这篇/这批论文讲了什么，我该带着什么目的去读 |
| 找精进点 | `gaps`（+ `coverage`/`topics` 语料自检） | 这个方向我能做什么 |
| 定实验 | `prereg` | 我的假设能不能证伪、对照齐不齐 |
| 核结果 | `verify` | 跑出来的结果是验证了主张，还是说不清 |

它和全链路科研轮子的分工：**生成类工作全部留给宿主 LLM**（总结、写作、判断），
fieldmate 只提供宿主没有的三样东西——领域缺陷库、三态判定（命中/未命中/无法判定）、
证据强度分级（STRONG/MEDIUM/WEAK）。工程纪律「判定归脚本、判断归 Prompt」不变，
但它只是内部实现原则，不再对外当卖点叙事（§5 红线）。

## 1. 更名方案：fieldmate → fieldmate

**时机：Phase 0 立即执行。** 现在零外部用户，改名零成本；PyPI 发布后再改就是破坏性变更。

| 项 | 改法 |
|---|---|
| PyPI 包名 | `fieldmate` → `fieldmate`，版本 0.3.0 |
| 包目录 | `fieldmate/` → `fieldmate/` |
| CLI 入口 | `fieldmate = fieldmate.cli:main`；`python -m fieldmate` |
| 环境变量 | `FIELDMATE_QUERY` → `FIELDMATE_QUERY` |
| 缓存目录 | `.fieldmate-cache/` → `.fieldmate-cache/`（旧缓存改名迁移或重建） |
| MCP server 名 | `"fieldmate"` → `"fieldmate"` |
| SKILL.md | `name:` 字段与正文同步 |
| 文档 | README 重写定位叙事（四步闭环，去「横向对比+缺陷库匹配」的工具味自述），全量替换自称 |
| 仓库 | GitHub 仓库名 `fieldmate` |

## 2. 领域范围（定稿）：只做 L1

**L1 = 相场几何处理**：AC/CH 曲面重建、点云/网格去噪、CH 图像分割。
依据：IMAVIS 在投稿、`Surface Restruction/` MATLAB 代码、pfdenoise 实验后端、
本地语料全部在这条线上——工作区的主要科研产出就是它。

- **L2（时间分数阶反应扩散）、L3（Langevin/阻尼相场）、金融线** → 进暂缓池（§7），
  不投入、不写检索词、不扩缺陷库。
- `libraries/queries.json` 保留 L1 相关子集（约 12 条：surface reconstruction /
  point cloud / mesh denoising / level set denoising / CH segmentation /
  mean curvature / volume preserving…），其余条目标注 `"deferred": true` 不删。
- bulk 语料抽出 L1 相关子集另存 `libraries/corpus_l1.json`（带 provenance 标注），
  247 篇全量不再作为分析口径。

**语料策略**：`corpus.json` 人工轨道仍是主力（L1 走期刊线，IMAVIS/CAD/CAGD 等不在 arXiv）。
新增 **OpenAlex/Crossref 元数据后端**（纯 urllib，stdlib）只为做一件事：
按 L1 检索词拉期刊线的 DOI/摘要/引用数，产出**候选清单供人工筛选**后进 corpus.json——
不做自动全文、不做无人复核的自动入库。

## 3. 交付形态（定稿）：skill / 子 Agent 优先

宿主 Harness（Claude Code、ZCode 等）里的使用形态，按优先级：

```
① skill 包（主入口）
   adapters/skill/SKILL.md  ← 重写为「四步闭环」编排剧本
   prompts/                 ← 宿主 LLM 执行的判断模板 + 候选 JSON schema
   doctor 自检命令
② 子 Agent 定义（宿主可 spawn 的 agent prompt 模板）
③ MCP server（第二形态，保留并补全入口）
④ 裸 CLI（确定性底座，以上三种形态都骑在它上面）
```

关键架构简化：**挂成 skill 后，宿主 LLM 本身就是「判断层」**——
skill 剧本明确告诉宿主哪些步骤由它执行（Assumption 槽精筛、语料子领域过滤、
最小验证实验设计），产出按候选 JSON schema 回填，框架只做 schema 校验与落盘，
人复核后才生效。独立跑 CLI 时才需要 `--llm-cmd`（Phase 3）。

## 4. 阶段路线（定稿，验收不过不进下一阶段）

### Phase 0 · 更名 + 地基（~1 周）

1. 执行 §1 更名清单（含 README 重写）。
2. **打包修复**：`libraries/` + `contracts/` 移入包内，`importlib.resources` 加载；
   新增 wheel 冒烟测试（干净 venv 安装 → 无关目录跑 `fieldmate list` 断言退出码 0）。
   *（实测：现状 `pip install` 后所有子命令 FileNotFoundError。）*
3. **stdout 编码防护**：`cli.main()` 入口 UTF-8 reconfigure + GBK 管道回归测试。
   *（实测：GBK 管道下 `UnicodeEncodeError`，宿主调用必崩。）*
4. git init + GitHub + CI（Ubuntu/Windows 双平台：pytest、ruff、wheel 冒烟）。

**验收**：干净环境 `pip install .` 后 12 个子命令全通；GBK 管道不崩；CI 双平台绿。

### Phase 1 · L1 语料纵深（2~3 周）

1. 语料收敛：建 `corpus_l1.json`（本地论文 + bulk L1 子集，带 provenance）。
2. OpenAlex/Crossref 元数据后端 + L1 候选清单工作流（机器拉清单 → 人筛 → 进 corpus）。
3. 缺陷库按 L1 扩容：曲面重建/点云网格去噪的评测协议缺陷、几何处理特有缺陷
   （以 IMAVIS 投稿与 pfdenoise 的实测教训为来源），入库硬标准不变（无可执行检测不入库）。
4. gold set 扩到 ≥20 篇 L1 论文（人工亲读，找组内第二标注者交叉 20%）。

**验收**：L1 语料 ≥50 篇可解析全文；报分辨率/开源自复现两项规则精确率 ≥0.7；
L1 相关缺陷 ≥25 条（现 17 条中多数已是 L1）。

### Phase 2 · skill / 子 Agent 交付（2 周）

1. SKILL.md 重写为四步闭环编排剧本：什么时候触发、先跑 `coverage` 还是直接 `read`、
   退出码怎么解读、哪些步骤宿主 LLM 自己做。
2. `prompts/` 落地为标准模板 + 候选 JSON schema（defect_extractor / purpose_router /
   exp_designer），宿主回填 → 框架校验落盘。
3. 子 Agent 定义模板（read-gaps-verify 编排者）。
4. `doctor` 子命令（编码/数据文件/缓存/网络体检，排障第一入口）。
5. 三种接入示例各配一个 5 分钟能跑通的 example（skill / MCP / subprocess）。

**验收**：在真实宿主 Harness 里零改动安装，一次对话内走完「读→挖→定→核」四步。

### Phase 3 · 判断分工硬化（1~2 周）

1. `--llm-cmd` 适配层（协议已在 `fieldmate/llm/__init__.py` 设计好，独立 CLI 场景用）。
2. Assumption/Gap 槽升级：正则出候选 → 宿主精筛 → 人复核（Assumption 槽现在是 0 命中）。
3. harvest 的子领域过滤交给宿主判断，判断记录进 rejection log。

**验收**：`--llm none` 全流程回归全绿（145 个测试）；Assumption 槽在 ≥3 篇真实 L1
论文上产出人确认有效的候选。

### Phase 4 · 闭环复利（长期，按需）

1. `verify` ↔ pfdenoise 对接：实验后端按 schema 产 `results.json`，跑完即核验。
2. 负结果回写缺陷库（REFUTED → 候选条目 → 人确认入库）。
3. 见 §7 暂缓池。

## 5. Non-goals 与定位红线

不做：通用文献问答/综述写作、端到端自动科研/写论文、内置 LLM、追语料规模、GUI。

**红线（本轮修正的教训）**：fieldmate 不做「审稿」——不给别人的论文打分、不下
「这篇论文好不好」的结论。三态判定与证据分级只回答一个问题：
**「我的下一步研究怎么做」**（哪里有缺口、我的实验有没有真验证主张）。
README 与 SKILL.md 的所有措辞按此校准；凡是读起来像「裁判别人论文」的表述都改掉。

## 6. 风险

| 风险 | 应对 |
|---|---|
| L1 期刊全文无公开接口 | 接受：OpenAlex 只做元数据层（分诊线索），全文走人工 corpus.json——这本来就是主轨道 |
| gold set 单人标注偏见 | Phase 1 引入组内第二标注者交叉 20% |
| skill 触发不稳 | SKILL.md description 用实测触发词打磨，Phase 2 验收时在真实宿主里试触发 |
| 更名牵连测试与脚本 | Phase 0 一并做，`grep -r fieldmate` 清零后才算完 |

## 7. 暂缓池（记录在案，不排期）

L2 时间分数阶反应扩散（arXiv 可自动扩，重启成本最低）→ L3 Langevin/阻尼相场
（需多源全文）→ 金融线（语料零覆盖）→ concept_graph / 期刊画像 → 自动全文多源。

## 8. 下一步

Phase 0 五件事（更名、打包、编码、git/CI、README）都是小时级工作量，方案已定稿，
按此执行即可。

## 9. 执行进度（2026-10-03 更新）

**Phase 0 全部完成**：
- ✅ git 仓库建立（历史干净，缓存不进库）
- ✅ 更名 fieldmate 全量落地，150 测试全绿
- ✅ stdout/stderr 强制 UTF-8 + ASCII 管道回归测试
- ✅ **数据随包分发**：libraries/contracts 移入 `fieldmate/`，`importlib.resources`
  加载（`_paths.py`）；**wheel 冒烟测试**（`scripts/wheel_smoke_test.py`）本地通过
  ——「pip install 后立刻可用」首次真正成立
- ✅ 真实测试六轮（宿主视角）抓出并修复：manifest 相对 cache_dir 两层静默丢全文、
  PDF 代理字符崩溃、`pdf` extra 缺 pymupdf 主力后端、磁盘缓存目录未随更名迁移

**Phase 2 大部分完成**：
- ✅ skill + 子 Agent 插件源（`plugins/fieldmate/`）+ 本地测试市场
- ✅ `doctor` 子命令（离线体检，`--net` 探测 arXiv）
- ✅ MCP 补全至 10 工具（prereg_init / evaluate_rules / read_card），去 `_tool` 后缀
- ✅ `docs/DESIGN.md`（H1–H6 契约 + 逐命令退出码表 + 数据布局 + 扩展规则）
- ✅ README 对齐实况（四步闭环定位、安装、退出码表、插件形态）
- ✅ CI workflow（`.github/workflows/ci.yml`：ubuntu+windows × pytest/ruff/wheel 冒烟）
- ✅ ruff 全绿（126 项清理：死变量、%-format、长行）
- ✅ read 重复卡片去重（语料与 --path 同篇只出一卡）

**待办（需用户动作或下阶段）**：
- ⬜ GitHub 远端创建与 push（`gh` 已登录，创建仓库属对外动作，等用户确认执行）
- ⬜ 插件市场 UI 安装与试用验收（用户手动三步）
- ⬜ Phase 1：gold set 扩容、缺陷库 L1 扩容、OpenAlex/Crossref 元数据后端
- ⬜ Phase 3：`--llm-cmd`、候选 schema 化；Phase 4：verify↔pfdenoise 对接、负结果回写
