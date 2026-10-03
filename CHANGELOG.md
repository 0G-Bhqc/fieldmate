# Changelog

## 0.5.0 (2026-10-03)

**判断层（Phase 3）——fieldmate 从脚本变成助手的关键增量**

- `--llm-cmd` 适配层：stdin/stdout JSON 协议接宿主任意 LLM 命令；不接则纯脚本照常可跑
- `read --refine-assumptions` 双路精筛：显式措辞走句级判断；期刊论文零显式措辞时
  自动切换 chunk-scan 找**隐式假设**（实测两篇真实论文全文仅 1 处 assume 族词）
- 幻觉闸门：LLM 返回的 quote 必须逐字命中喂给它的文本，否则丢弃并记账
- `harvest --llm-cmd` 可选闸门 C：子领域过滤，判断带理由进 rejection log
- 任务模板随包（`fieldmate/prompts/`：assumption_refine / assumption_scan / subdomain_filter）
- 修复存量 bug：harvest 词法闸门 `STRONG_TERMS` 大小写失效（Allen-Cahn 等 8 条
  强术语从未匹配过）
- doctor 新增 query_lexicon / llm_prompts 资产检查；CLI `--version` 旗标
- 测试 151 → 169

## 0.4.0 (2026-10-03)

**工程地基——「pip install 后立即可用」从口号变成事实**

- 数据随包分发：`libraries/`、`contracts/` 移入包内，`importlib.resources` 统一加载；
  wheel 冒烟测试（干净 venv 非 editable 安装 + 无关 cwd 全子命令）
- 更名：research-harness/rharness → **fieldmate**（包、CLI、环境变量 `FIELDMATE_QUERY`、
  缓存 `.fieldmate-cache/`）
- `doctor` 子命令（离线体检）；MCP 补全至 10 工具；`docs/DESIGN.md`（H1–H6 契约 +
  逐命令退出码表）
- CLI stdout/stderr 强制 UTF-8（修 Windows GBK 管道下宿主 subprocess 必崩）
- 六轮宿主视角真实测试抓出并修复：manifest 相对 cache_dir 两层静默丢全文、
  PDF 代理字符崩溃、`pdf` extra 缺 pymupdf 主力后端（pypdf-only 时 72% 语料不可解析）
- ZCode 插件源（skill + 子 Agent）+ 本地市场 + `install_skill.py` 零 UI 工作区挂载器
- 测试 145 → 151

## 0.3.0 (2026-10-03)

- 更名 fieldmate（对外叙事同步：相场/几何处理科研助手四步闭环；红线：不审稿）
- git 仓库建立与 CI（ubuntu/windows × pytest/ruff/wheel 冒烟）

## 0.2.0 (2026-10-02)

- 基线（research-harness）：三能力内核 + 缺陷库 19 条 + 145 个测试
