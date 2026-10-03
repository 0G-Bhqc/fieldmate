"""全文五槽抽取（L2）—— 从论文正文里挖出「精进点矿脉」

为什么要槽位化
--------------
摘要式总结产出的是「这篇论文做了什么」。而精进点需要的是
「这篇论文**没验证什么**」——后者在摘要里系统性缺席。

五个槽，各自对应一类信息：

    Claim       它声称什么
    Mechanism   靠什么机制实现
    Evidence    用什么证据支撑（数据集/对照/指标）
    Assumption  隐含但未验证的前提　←★ 精进点的主要矿脉
    Protocol    评测协议（数据/分辨率/噪声/指标/基线）
    Gap         作者自己承认的局限

设计立场：**只抽「候选句」，不做判断。**
本模块输出「这句话可能是假设」+ 原句 + 位置，由人或 agent 复核。
理由与 docs/PLAN.md §1 一致：判定归脚本，判断归 prompt。
而且假设这类句子**必须人读**——「we assume the material is homogeneous」和
「we assume readers know PDEs」的学术价值天差地别，正则判不了。

假设句的识别用 NLP 里的 hedge/assumption 标记词法，这是成熟做法：
  显式假设：we assume / assuming / is assumed / under the assumption
  隐含假设：it is well known that / we neglect / we ignore / restrict to /
            we consider only / provided that / for simplicity
这类句子在实证类论文里密度高但**很少被验证**，是天然的可发表切入点。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["Slot", "PaperSlots", "extract_slots", "SECTION_MAP", "ASSUMPTION_MARKERS"]

# ---- 章节识别 ----
# ⚠ 必须严格。首版用宽松匹配，结果一篇论文里 "method" 命中 17 次 ——
# 因为正文里以数字/公式开头的行（"2. Numerical discretization..."、
# "1.2 The grid sizes..."）全被当成章节标题。教训：
# **章节标题必须短、必须不以数字开头、必须整行只有标题**。
SECTION_PATTERNS = [
    ("abstract",   r"^(?:an\s+)?abstract\s*[:.]?\s*$"),
    ("intro",      r"^(?:\d+\.?)?\s*introduction\s*$"),
    ("related",    r"^(?:\d+\.?)?\s*related\s+work\s*$"),
    ("method",     r"^(?:\d+(?:\.\d+)*\.?\s+)?"
                    r"(?:numerical\s+(?:discretization|method|experiments?|results?)|"
                    r"discretization|formulation|governing\s+equations?|"
                    r"proposed\s+(?:method|model|approach)|methodology|algorithm)\s*[:.]?\s*$"),
    ("experiment", r"^(?:\d+\.?)?\s*(?:numerical\s+experiments?|experiments?|"
                    r"results?|evaluation|simulations?)\s*[:.]?\s*$"),
    ("conclusion", r"^(?:\d+\.?)?\s*(?:conclusions?|discussion|summary|"
                    r"conclusion\s+and\s+\w+|future\s+work|limitations?)\s*[:.]?\s*$"),
    ("ref",        r"^(?:references|bibliography)\s*$"),
]

# ⚠ 必须严格。首版用宽松匹配，结果一篇论文里 "method" 命中 17 次 ——
# 因为正文里以数字/公式开头的行全被当成章节标题。
# 教训：**章节标题要短（≤8 词）、不以数字开头的句子开头**。
# v2 又甩到另一头：锚定 `[:.]?\s*$` 后，
# 真实标题 `3. Numerical discretization for 3D reconstruction` 这种
# 「关键词 + 尾描述」形式全被漏掉，method/experiment 整段消失。
# v3 允许尾描述，但**仍要求关键词出现在行首附近**。
_TAIL = r"(?:\s+(?:for|of|on|in|to|with|via|and)\s+[\w\- ]+)?"
SECTION_PATTERNS = [
    ("abstract",   r"^(?:an\s+)?abstract\s*[:.]?\s*$"),
    ("intro",      r"^(?:\d+\.?)?\s*introduction\s*[:.]?\s*$"),
    ("related",    r"^(?:\d+\.?)?\s*related\s+work\s*[:.]?\s*$"),
    ("method",     r"^(?:\d+(?:\.\d+)*\.?\s+)?"
                    r"(?:numerical\s+(?:discretization|method|formulation|scheme)|"
                    r"discretization|formulation|governing\s+equations?|"
                    r"proposed\s+(?:method|model|approach)|methodology|algorithm|"
                    r"mathematical\s+\w+)\s*[:.]?\s*" + _TAIL + r"\s*$"),
    ("experiment", r"^(?:\d+(?:\.\d+)*\.?\s+)?"
                    r"(?:numerical\s+(?:experiments?|results?)|experiments?|results?|"
                    r"evaluation|simulations?)\s*[:.]?\s*" + _TAIL + r"\s*$"),
    # ---- ML / 实验类论文的章节词汇（实测 2609.21512 FrFNO：76 个标题只归类出 2 个）
    #
    # 上一版的词表是照着**数值分析**论文写的（introduction / discretization /
    # numerical experiments）。而语料里 21 篇是 ML 侧的，它们的章节叫
    # Architecture / Training / Datasets / Ablation —— 一个都对不上，
    # 于是那 21 篇的 method 段全部丢失，Mechanism 槽跟着掉。
    #
    # 实测这个瓶颈**不在「能不能找到标题」，而在「找到之后归不归得了类」**：
    # 装上结构后端后该论文有 76 个干净标题，但按旧词表只能归出 2 个。
    # 也就是说：花依赖把标题检测做对了，还得把词表补齐才能兑现 ——
    # 而补词表是**纯 stdlib 改动**，不要钱。
    #
    # 风险控制沿用既有纪律：这些词在**裸文本**里仍然要求「有编号」或「独占一行」
    # （见 _heading_at 的白名单），所以 "Model." 开头的正文续行不会被误收；
    # 在 Markdown 里整行就是标题，误判风险更低。
    ("method",     r"^(?:\d+(?:\.\d+)*\.?\s+)?"
                    r"(?:architecture|network\s+architecture|model\s+design|"
                    r"framework|formulation|training|optimiz\w*|implementation|"
                    r"our\s+approach|the\s+proposed\s+\w+|preliminaries|"
                    r"problem\s+formulation|mathematical\s+\w+)\s*[:.]?\s*"
                    + _TAIL + r"\s*$"),
    ("experiment", r"^(?:\d+(?:\.\d+)*\.?\s+)?"
                    r"(?:experimental\s+\w+|experiments?|results?|ablation(?:\s+study)?|"
                    r"datasets?|data\s+description|benchmark|evaluation|simulations?|"
                    r"case\s+stud(?:y|ies)|performance)\s*[:.]?\s*" + _TAIL + r"\s*$"),
    ("related",    r"^(?:\d+(?:\.\d+)*\.?\s+)?"
                    r"(?:related\s+work|background|prior\s+work|related\s+studies)\s*"
                    r"[:.]?\s*" + _TAIL + r"\s*$"),
    ("conclusion", r"^(?:\d+(?:\.\d+)*\.?\s+)?"
                    r"(?:conclusions?|discussion|summary|future\s+work|limitations?|"
                    r"outlook|final\s+remarks)\s*[:.]?\s*" + _TAIL + r"\s*$"),
    ("ref",        r"^(?:references|bibliography)\s*$"),
]

# 槽位落在哪些章节
SECTION_MAP: dict[str, tuple[str, ...]] = {
    "Claim": ("abstract", "intro", "conclusion"),
    "Mechanism": ("method",),
    "Evidence": ("experiment", "abstract", "intro"),
    "Assumption": ("method", "intro", "experiment", "conclusion"),
    "Protocol": ("experiment", "method", "abstract"),
    "Gap": ("conclusion", "experiment", "intro"),
}

# ---- 假设 / 缺口 / 协议的句法标记 ----
# ⚠ 只收**真正带假设语义**的标记。首版混进了
#    `in this section, we ...` 这类纯话语标记，抽出「In this section, we introduce
#    the numerical」当假设句 —— 纯噪声。
#    判据：这句话里必须出现「假设/忽略/限定/为简化起见」这类**让步或限定语义**。
ASSUMPTION_MARKERS = [
    r"we assume", r"\bassum(?:e|es|ing|ption|ptions|t)\b", r"assuming",
    r"under the assumption", r"is well[- ]known that", r"it is well[- ]known",
    r"we neglect", r"we ignore", r"\bneglect\w*\s+the", r"\bignor(?:e|es|ing)\s+the",
    r"restrict(?:ed|ing|s)?\s+to\s+(?:our|the)\s+(?:case|study|work|isotropic|2D|3D)",
    r"we consider only", r"for simplicity", r"provided that", r"as long as",
    r"is beyond the scope", r"is left (?:to|for) future", r"remains? (?:open|an open)",
    r"for (?:ease|convenience) of (?:presentation|discussion)",
]
GAP_MARKERS = [
    r"\blimitation", r"\bdrawback", r"\bhowever\b", r"\bnevertheless",
    r"\bin future work\b", r"\bfuture (?:work|research|extensions?)\b",
    r"\bremains? (?:open|challenging|an open)", r"\bextend\w*\s+to\b",
    r"does not (?:consider|handle|account|address|guarantee)",
    r"cannot be (?:applied|extended|guaranteed)", r"is (?:not|yet) (?:been )?general",
    r"\bwe do not\b", r"\bout of scope\b", r"\bfails? to\b",
    r"\bnot (?:always|guaranteed|sufficient)\b",
]
CLAIM_MARKERS = [
    r"we (?:propose|present|introduce|develop|derive|design|construct)\b",
    r"this (?:paper|work) (?:proposes|presents|introduces|addresses|investigates)",
    r"we (?:show|demonstrate|prove) that",
]
# 协议里含「我们设了网格/时间步」这类**参数设定**语句
PROTOCOL_MARKERS = [
    r"we (?:conduct|perform|carry out|run) (?:the )?(?:experiments?|simulations?|tests?|cases?)",
    r"experiments? (?:are|were) (?:conducted|carried out|performed|done)",
    r"we (?:evaluate|compare|benchmark|test|apply) (?:it|the|our|on|against|to)",
    r"we use the (?:following )?(?:dataset|benchmark|model|test|scheme)",
    r"the (?:error|accuracy|metric|measure) is (?:measured|computed|defined|evaluated)",
    r"we (?:set|choose|take|adopt|employ)\s+the\s+\w+",
    r"the (?:grid|mesh|resolution|time step|step size|domain) (?:is|are|of|as) ",
    r"\bN\s*=\s*N\s*[xyz]\b", r"h\s*=\s*[\d(]", r"Δt\s*=|∆t\s*=",
]

CLEAN = re.compile(r"\s+")
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")

# 行首的编号前缀：1. / 3.2 / 2.1. / IV. / A. / (ii)
_LEAD_NUM = re.compile(r"^(?:\(?\d+(?:\.\d+)*\)?\.?|[IVXLC]+\.?|[A-H]\.)\s+")
# Markdown 标题：## / ### … 后面跟标题文字。**有它就不用猜标题**。
# 允许行内 `**强调**` —— pymupdf4llm 对 "## **Abstract**" 就是这么输出的。
_MD_HEADING = re.compile(r"^#{1,6}\s+(\S.*?)\s*$")

# 关键词前缀 = SECTION_PATTERNS 去掉可选尾描述、再去掉行尾锚。
# 两步都必要：带 _TAIL 的 pattern 尾部是 `\s*[:.]?\s*` + _TAIL + `\s*$`，
# 不带 _TAIL 的（intro/abstract/related）尾部直接是 `\s*$`。
# 只去其中一样，另一半会留下来把 pattern 钉死成「永远匹配不上」。
# 在模块加载时算一次，不另写一份词表。
_HEAD_PREFIX: list[tuple[str, re.Pattern[str]]] = [
    (name, re.compile(pat.replace(_TAIL, "").replace(r"\s*$", "") + r"\s*[:.]?\s*",
                      re.IGNORECASE))
    for name, pat in SECTION_PATTERNS
]

# 只在「有章节编号」或「标题独占一行」时相信非结构性标题（method / experiment 家族）。
#
# 理由：真实 PDF 里有大量以 discretization / formulation / algorithm / experiment
# 开头的**正文续行**（上一行句子被换行切断）。黑名单补不完 —— 可疑词就是
# 「所有非结构性标题」，所以直接用白名单：只有 abstract / introduction /
# conclusions / references 这一类结构性标题才可能在正文里以它开头。
# 漏掉的 method 章节由 extract_slots 的按槽退化兜住，不会交白卷。
_STRUCTURAL = re.compile(
    r"^(?:abstract|introduction|related\s+work|conclusions?|discussion|summary|"
    r"future\s+work|limitations?|references|bibliography|background)$")


def _heading_at(line: str) -> tuple[str, int] | None:
    r"""这一行是不是以章节标题开头？返回 (章节名, 标题结束偏移)。

    为什么需要这个函数，而不是直接拿 SECTION_PATTERNS 去 match 整行
    --------------------------------------------------------------
    实测 247 篇全文：章节标题**几乎总是和首段正文挤在同一行**，因为
    PyMuPDF 的块抽取把标题和紧随其后的段落合成了一个文本行：

        "1. Introduction. In this paper, we focus on numerical investigation on ..."
        "4. Numerical results. In this section, we present some numerical experiments"
        "Abstract. As a variational phase-field model, the time-fractional Allen-Cahn ..."

    量化：行首能匹配到章节关键词的有 246/247 篇（100%），
    其中 233 行是「标题后紧跟正文」；**假阳性 0 例**。
    而旧实现要求整行只有标题（78 字符 / 11 词上限，且明写「不以数字开头」），
    结果 method 只在 29/247（12%）的论文里检出。

    注意旧实现的两处自相矛盾：预过滤器拒绝「数字开头」的行，
    而 SECTION_PATTERNS 自己却允许前置编号（如 `(?:\d+\.?)?\s*introduction`）
    —— 两边互相拆台，谁也不可能赢。

    假阳性为什么是 0：要求**关键词紧跟在行首编号之后**。
    正文里 "2. The grid sizes are ..." 的编号后是 "The"，匹配不上。
    首版宽松匹配的教训（`method` 命中 17 次，正文里以数字/公式开头的行
    全被当标题）正是丢掉了这个约束。

    尾描述怎么处理
    ------------
    SECTION_PATTERNS 里那段可选尾描述（"for 3D reconstruction" 之类）
    在这里是**负担而不是帮助**：它和前面的 `\s*[:.]?\s*` 抢同一个空白，
    于是 "Numerical results. In this section" 反而匹配不上（实测踩到）。
    所以这里只用**关键词前缀**，尾描述交给下面的句界截断处理。
    截断只影响「首段正文从哪开始」，不影响章节名，遇到缩写切早一点可接受。

    裸关键词为什么还要再筛一道
    ------------------------
    「行首有章节关键词 -> 无假阳性」这个结论**是错的**，第一版诊断就是这么
    漏掉的，因为它只把「关键词后直接跟小写词」当假阳性。真实的假阳性长这样：

        L18  "proposed model. Statistic metrics such as ..."      -> 误判 method
        L82  "algorithm based on a modified AC equation. ..."    -> 误判 method
        L465 "experiment in this paper, we take the same ..."     -> 误判 experiment
        L580 "proposed method performs better than the other ..." -> 误判 method

    根因：PDF 换行把句子切断，续行恰好以 algorithm / experiment / proposed method
    开头。续句首字母大写，所以靠大小写分辨不出来。

    判据改成两条更强的：
      a. 行首有章节编号            -> 强信号，直接认
      b. 标题延伸到行尾（身后无正文）-> 认
      c. 其余（编号缺失 + 同行有正文）-> 只认「即使在正文里也不太会以它开头」的
         关键词（introduction / abstract / conclusions / references /
         numerical method / discretization / formulation ...）。
         `algorithm`/`experiment`/`proposed method` 这类留在这里就是噪声源。

    关键词前缀由 SECTION_PATTERNS 去掉尾描述生成，**不另立一份词表** ——
    两处各写一份，迟早会漂移成两个不同的「什么算章节标题」。

    Markdown 路径（装了 pymupdf4llm 时）
    --------------------------------
    上面这一整套启发式都是为**裸文本**打的补丁 —— 因为裸文本里标题和正文
    长得一样，只能靠「行首 + 编号 + 短行 + 词表」去猜，还必须防正文续行
    冒充标题（见上面的 L18/L82/L465/L580）。

    结构后端给的 Markdown 里，标题是 `## 3. Numerical discretization` 这种
    **带 `#` 的显式标记**，不用猜。实测同一批 20 篇：
        标题总数      85 -> 296
        method 检出   3/20 -> 13/20
    所以有 `#` 就直接采信，同时剥掉 Markdown 强调标记（`**Abstract**`）
    和编号，好让下面那套词表照样能用。
    """
    s = line.lstrip()
    if not s:
        return None
    m = _MD_HEADING.match(s)
    if m:
        # 采信显式标记，剥掉 `##` / `**` / 编号，只把标题文字交给词表
        inner = re.sub(r"^[_*\s]+|[_*\s]+$", "", m.group(1))
        lm = _LEAD_NUM.match(inner)
        if lm:
            inner = inner[lm.end():]
        for name, pat in _HEAD_PREFIX:
            if pat.match(inner):
                return name, len(line)
        # 带 `#` 但词表不认（如 "Acknowledgment"、"4.1. Parameter test"）
        # -> 返回 None，**不硬塞进某个族**。塞错族比漏掉更糟：
        # 它会把「这一节其实是实验」记成「这一节是方法」，
        # 污染该节全部候选句的落点统计，而且没有任何东西会报错。
        return None
    lead = _LEAD_NUM.match(s)
    pos = lead.end() if lead else 0
    body = s[pos:]
    for name, pat in _HEAD_PREFIX:
        m = pat.match(body)
        if not m:
            continue
        head = body[:m.end()]
        if len(head) > 90 or len(head.split()) > 14:
            continue
        # 标题边界就是 m.end()，**不需要**再往后搜一次句界。
        #
        # 踩过的坑：pattern 尾部自带 `\s*[:.]?\s*`，它已经把标题后面那个句号
        # 连同空格一起吞掉了；再从 m.end() 起搜 `[.:]\s`，只会找到**下一句**的
        # 句号。于是 "1. Introduction. In this paper we study the problem carefully."
        # 的 off 被算到行尾，`off < len(line)` 判 false，同行正文被整段丢弃。
        off = pos + m.end()
        # m.group(0) 里含被 pattern 顺带吞掉的句末标点，必须先剥掉，
        # 否则 "proposed model." 永远匹配不上 ^proposed\s+model$。
        kw = re.sub(r"[\s:.]+$", "", m.group(0).strip().lower())
        line_final = m.end() >= len(body.rstrip())
        if not (lead or line_final or _STRUCTURAL.match(kw)):
            # 三条都不满足 -> 多半是 PDF 换行切断的正文续行，放行就是制造假章节。
            #
            # 这里试过再加一条「上一行以句末标点收尾 + 关键词后带句号」的结构判据，
            # 实测在 247 篇语料上只换来 method 19%->20%、Protocol +0.8pp，
            # 却让 `prev` 缺省为 "" 时（等于宣称「这里是段落开头」）
            # 放行了 "proposed model. Statistic metrics ..." 这类误判。
            # 代价大于收益，已撤掉。漏掉的 method 章节由按槽退化兜住。
            return None
        return name, off
    return None


def _sentences(text: str, min_len: int = 30, max_len: int = 400) -> list[str]:
    out = []
    for s in _SENT.split(text):
        s = CLEAN.sub(" ", s).strip()
        if min_len <= len(s) <= max_len:
            out.append(s)
    return out


def _sections(text: str) -> list[tuple[str, str]]:
    """返回 [(section_name, body)]，按行首的章节标题切分。

    两个必须做的事：
      1. **只认「整行就是标题」的短行**（见 SECTION_PATTERNS 的注释）。
         宽松匹配会把正文里以数字/公式开头的行误判为标题。
      2. **合并同名相邻段**。PDF 抽取出来的文本里
         "Numerical discretization" 会在方法段里反复出现，
         不合并就会得到 [method, method, method, ...]，
         槽位落点统计随之失真。
    """
    lines = text.splitlines()
    # marks[i] = (行号, 章节名, 标题在该行内的结束偏移)
    # 偏移用来把「标题 + 首段正文」那一行拆开，否则首段会被当成标题丢掉。
    marks: list[tuple[int, str, int]] = []
    for i, line in enumerate(lines[:4000]):
        hit = _heading_at(line)
        if hit:
            marks.append((i, hit[0], hit[1]))
    # 合并相邻同名；同名的中间夹了别的段就不合并
    merged: list[tuple[int, str, int]] = []
    for m in marks:
        if merged and merged[-1][1] == m[1]:
            continue
        if any(x[1] == m[1] for x in merged):
            # 已出现过同名且不连续：保留第一个，忽略后续（宁可漏不可碎）
            continue
        merged.append(m)
    if not merged:
        return [("abstract", text)]
    secs: list[tuple[str, str]] = []
    for k, (start, name, off) in enumerate(merged):
        end = merged[k + 1][0] if k + 1 < len(merged) else len(lines)
        if off < len(lines[start]):
            # 标题与首段正文同行：只取正文部分，**不要**再把整行拼一遍
            # （整行已含标题，重复拼接会让同一段话出现两次，
            #   句切分时又被 \n 劈开，正好把一个完整句子腰斩）。
            body = "\n".join([lines[start][off:].strip()] + lines[start + 1:end])
        else:
            body = "\n".join(lines[start + 1:end])
        if name != "ref":
            secs.append((name, body))
    # 退化保护：识别到的章节覆盖不到全文的 30% 时，说明切分不可靠
    # （PDF 抽取的文本里章节标题格式千奇百怪，正则不可能对所有刊都成立）。
    # 此时**退回全文扫描** —— 槽位匹配本身不依赖章节，只依赖句法标记。
    # 实测：退回全文后 Protocol 槽能准确抓到 h=(b-a)/Nx、Δt=T/Nt、Neumann 边界，
    # 而强行按错误的章节切分会漏掉它们。
    covered = sum(len(b) for _, b in secs)
    if covered < 0.3 * len(text):
        return [("abstract", text)]
    return secs


def _has(sent: str, markers: list[str]) -> str | None:
    low = sent.lower()
    for m in markers:
        if re.search(m, low):
            return m
    return None


@dataclass
class Slot:
    name: str = ""
    items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "n": len(self.items), "items": self.items}


@dataclass
class PaperSlots:
    paper_id: str
    title: str
    sections: list[str] = field(default_factory=list)
    n_chars: int = 0
    slots: dict[str, Slot] = field(default_factory=dict)
    # 哪些槽位因为落点章节没识别出来而放宽到了全文。
    # 必须报出来：否则「抓到了 3 条 Mechanism 候选」会被当成
    # 「这篇论文的方法段里明确写了这三条」，而实际是全文里碰巧撞上的。
    degraded_slots: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"paper_id": self.paper_id, "title": self.title,
                "sections": self.sections, "n_chars": self.n_chars,
                "degraded_slots": self.degraded_slots,
                "slots": {k: v.to_dict() for k, v in self.slots.items()}}


def extract_slots(paper, text: str, per_slot: int = 4) -> PaperSlots:
    """从全文抽五槽候选句。只抽不判。"""
    ps = PaperSlots(paper_id=paper.id, title=paper.title, n_chars=len(text or ""))
    if not text:
        return ps
    secs = _sections(text)
    ps.sections = [n for n, _ in secs]

    # 先把每个槽位的落点章节收窄到**实际存在**的那些。
    #
    # 为什么要有这一步（实测）：Mechanism 只认 method 一节，而 247 篇里
    # 有 118 篇没被认出 method 章节 —— 章节正则不可能对所有刊成立。
    # 早先版本只有一个全局退化条件（单章节 + >8000 字符 -> 全文扫描），
    # 章节切分修好之后它几乎不再触发，Mechanism 命中率反而从 43% 掉到 31%：
    # 以前是靠「退化成全文扫描」蒙对的，不是本来就对。
    #
    # 改成**按槽退化**：某个槽位的落点章节一个都没识别出来时，
    # 单独把这一槽放宽到全文。这与本模块开头写的立场一致 ——
    # 「槽位匹配本身不依赖章节，只依赖句法标记」。
    # 退化只放宽搜索范围，不放宽判据，所以不会凭空造出候选句。
    present = {n for n, _ in secs}
    whole_text_fallback = len(secs) == 1 and secs[0][0] == "abstract" and len(text) > 8000
    # 退化目标 = **所有已识别章节**（等价于全文扫描）。
    # 注意不能写 tuple(SECTION_MAP)：那是槽名（Claim/Assumption/...），
    # 而下面 `name` 是章节名，两者永不相等，会把所有句子都过滤掉，
    # 让退化的论文反而一个候选都抽不出来。踩过。
    every_section = tuple(n for n, _ in secs)
    allowed_for: dict[str, tuple[str, ...]] = {}
    for slot, want in SECTION_MAP.items():
        got = tuple(n for n in want if n in present)
        if got and not whole_text_fallback:
            allowed_for[slot] = got
        else:
            allowed_for[slot] = every_section
            ps.degraded_slots.append(slot)

    buckets: dict[str, list[dict[str, Any]]] = {k: [] for k in SECTION_MAP}
    for name, body in secs:
        for sent in _sentences(body):
            for slot, allowed in allowed_for.items():
                if name not in allowed:
                    continue
                mk = {
                    "Claim": CLAIM_MARKERS, "Mechanism": None,
                    "Evidence": PROTOCOL_MARKERS, "Assumption": ASSUMPTION_MARKERS,
                    "Protocol": PROTOCOL_MARKERS, "Gap": GAP_MARKERS,
                }[slot]
                if slot == "Mechanism":
                    ok = bool(re.search(
                        r"(is|are) (?:given by|governed by|defined by|written as|solved|"
                        r"integrated|discretized|formulated|described as|computed as)",
                        sent, re.IGNORECASE))
                    m = "mechanism"
                else:
                    m = _has(sent, mk)
                    ok = m is not None
                if not ok:
                    continue
                # 去重：同槽位内句子高度相似就只留一条
                key = " ".join(sorted(set(sent.lower().split())))[:120]
                if any(b.get("key") == key for b in buckets[slot]):
                    continue
                buckets[slot].append({"sentence": sent, "section": name,
                                      "marker": m, "key": key})

    for slot, items in buckets.items():
        # Assumption / Gap 优先（它们才是精进点来源），再按出现顺序
        items.sort(key=lambda b: 0 if slot in ("Assumption", "Gap") else 1)
        ps.slots[slot] = Slot(name=slot,
                              items=[{k: v for k, v in b.items() if k != "key"}
                                     for b in items[:per_slot]])
    return ps


def cross_assumptions(slots_list: list[PaperSlots], min_papers: int = 2) -> list[dict]:
    """跨论文的共同假设 —— 「N 篇都假设了 X，但没人验证」是最典型的立项切口。

    ⚠ 当前的关键词是「前 8 个词排序后取键」，这是**粗筛**：
    同一句式（"we assume that the"）会让语义完全不同的句子撞到同一个键上。
    所以输出必须人读确认，不能直接当结论。
    要可靠只能上句向量（embedding），那会破坏「纯 stdlib 零依赖」这条硬约束。
    """
    from collections import Counter
    c: Counter = Counter()
    where: dict[str, list[str]] = {}
    for ps in slots_list:
        for it in ps.slots.get("Assumption", Slot()).items:
            key = " ".join(sorted(set(it["sentence"].lower().split())[:8]))
            c[key] += 1
            where.setdefault(key, []).append(ps.paper_id)
    return [{"shared_by": n, "papers": where[k], "key": k}
            for k, n in c.most_common() if n >= min_papers]
