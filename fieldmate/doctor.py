"""doctor —— fieldmate 自身体检（H6 失败显式化的排障第一入口）。

设计
----
* 默认**纯离线**（H3：不接 LLM 也能跑的姊妹约束——不联网也能体检）；
  `--net` 才探测 arXiv 可达性。
* 检查项分层：**核心**（数据资产、包完整性）坏了退出码 2；
  **提示**（PDF 后端、缓存、编码）只报告不致命。
* 判定归脚本：每一项都是确定性检查，结果结构化（--json），宿主 harness
  可以直接按字段分支，不用解析散文。
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass

EXIT_OK = 0
EXIT_SOURCE = 2      # 核心数据缺失 = 数据源级失败，与 cli 的 EXIT_SOURCE 同语义
NET_TIMEOUT = 5.0


@dataclass
class Check:
    name: str
    ok: bool
    fatal: bool          # fatal 且失败 → 退出码 2
    detail: str

    def as_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok,
                "fatal": self.fatal, "detail": self.detail}


def collect_report(include_net: bool = False) -> tuple[list[Check], dict]:
    """执行全部检查，返回 (checks, info)。info 是非判定的补充信息。"""
    checks: list[Check] = []
    info: dict = {}

    # ---- 1) 运行时与包 ------------------------------------------------
    from . import __version__
    info["python"] = sys.version.split()[0]
    info["fieldmate"] = __version__
    checks.append(Check("runtime", True, False,
                        f"Python {info['python']} / fieldmate {__version__}"))

    # ---- 2) 输出编码（宿主 subprocess 捕获的第一现场）------------------
    enc = getattr(sys.stdout, "encoding", None) or "?"
    try:
        "fieldmate✓".encode(enc or "ascii")
        enc_note = f"stdout={enc}，样例字符可编码"
    except (UnicodeEncodeError, LookupError):
        enc_note = (f"stdout={enc} 不能编码样例字符 —— CLI 已在入口强制 UTF-8+replace，"
                    f"宿主侧无需设置 PYTHONIOENCODING")
    checks.append(Check("output_encoding", True, False, enc_note))

    # ---- 3) 包内数据资产（fatal）---------------------------------------
    from ._paths import contracts_dir, library_dir
    try:
        lib_dir = library_dir()
        from .match.patterns import load_library, load_rules
        lib = load_library()
        rules = load_rules()
        n_rules = len(rules.get("rules", []))
        rule_ids = {r["id"] for r in rules.get("rules", [])}
        missing = [d["id"] for d in lib if d["id"] not in rule_ids]
        checks.append(Check("defect_library", len(lib) > 0, True,
                            f"{len(lib)} 条缺陷 / {n_rules} 条可执行规则"
                            + (f"；{len(missing)} 条暂无规则" if missing else "")))
        info["n_defects"], info["n_rules"] = len(lib), n_rules

        from .eval.prf import load_corpus, load_goldset
        gold = load_goldset()
        corpus = load_corpus()
        checks.append(Check("goldset", len(gold) > 0, True,
                            f"{len(gold)} 条人工标注"))
        has_corpus = bool(corpus.get("papers") or corpus.get("arxiv_papers"))
        n_local = len(corpus.get("papers") or {})
        n_arxiv = len(corpus.get("arxiv_papers") or [])
        checks.append(Check("corpus_manifest", has_corpus, True,
                            f"corpus.json：{n_local} 篇本地目标论文，{n_arxiv} 篇 arXiv 目标论文"))
        manifests = sorted(p.name for p in lib_dir.glob("corpus_*.json"))
        info["manifests"] = manifests
        checks.append(Check("bulk_manifests", True, False,
                            "包内 manifest：" + ", ".join(manifests)))
        qpath = lib_dir / "queries.json"
        from .sources.corpus import _load_queries
        n_q = len(_load_queries(qpath, None))
        checks.append(Check("query_lexicon", n_q > 0, True,
                            f"检索词汇表：{n_q} 条实测检索式"))
        prompts_dir = lib_dir.parent / "prompts"
        n_prompts = len(list(prompts_dir.glob("*.md"))) if prompts_dir.is_dir() else 0
        checks.append(Check("llm_prompts", n_prompts >= 3, True,
                            f"判断层任务模板：{n_prompts} 份"
                            + ("" if n_prompts >= 3 else "（--llm-cmd 判断层不可用）")))
        info["n_prompts"] = n_prompts
        _ = contracts_dir  # load_rules 间接验证；保留引用以免误删导入
    except Exception as e:                                  # noqa: BLE001
        checks.append(Check("data_assets", False, True, f"包内数据加载失败：{e}"))

    # ---- 4) PDF 解析后端（提示）----------------------------------------
    try:
        from .sources.local import available_backends
        backends = available_backends()
        ok = bool(backends)
        detail = (", ".join(backends) if backends
                  else "无 —— read/--fulltext 将报「无法解析」；pip install -e \".[pdf]\"")
        checks.append(Check("pdf_backends", ok, False, detail))
        info["pdf_backends"] = backends
    except Exception as e:                                  # noqa: BLE001
        checks.append(Check("pdf_backends", False, False, f"枚举失败：{e}"))

    # ---- 5) 缓存（提示）-------------------------------------------------
    from .sources.fulltext import default_cache
    cache = default_cache()
    n_pdf = n_txt = 0
    if cache.is_dir():
        n_pdf = len(list(cache.glob("*.pdf")))
        txt_dir = cache.parent / "parsed_text"
        n_txt = len(list(txt_dir.glob("*.txt"))) if txt_dir.is_dir() else 0
    checks.append(Check("pdf_cache", True, False,
                        f"{cache}（绝对路径会随 cwd 变化）：PDF {n_pdf} 个，解析缓存 {n_txt} 份"
                        + ("" if n_pdf else "；空缓存属正常（--fulltext 首次抓取后填充）")))
    info["cache"] = {"path": str(cache), "n_pdf": n_pdf, "n_parsed": n_txt}

    # ---- 6) 网络（仅 --net）--------------------------------------------
    if include_net:
        try:
            import urllib.request
            req = urllib.request.Request(
                "https://export.arxiv.org/api/query?search_query=all:fieldmate&max_results=1",
                headers={"User-Agent": f"fieldmate-doctor/{__version__}"})
            with urllib.request.urlopen(req, timeout=NET_TIMEOUT) as resp:
                ok = resp.status == 200
            checks.append(Check("arxiv_reachable", ok, False,
                                f"export.arxiv.org 可达（HTTP {resp.status}）"
                                if ok else "export.arxiv.org 返回异常状态"))
        except Exception as e:                              # noqa: BLE001
            checks.append(Check("arxiv_reachable", False, False,
                                f"不可达：{type(e).__name__} —— "
                                f"离线用法（--corpus/--path）不受影响"))

    return checks, info


def report_markdown(checks: list[Check], info: dict) -> str:
    lines = ["## fieldmate doctor —— 环境体检", ""]
    for c in checks:
        mark = "✅" if c.ok else ("⛔" if c.fatal else "⚠️")
        lines.append(f"- {mark} **{c.name}**　{c.detail}")
    fatal_bad = any(c.fatal and not c.ok for c in checks)
    lines.append("")
    if fatal_bad:
        lines.append("> ⛔ 存在致命失败：包数据不完整，多数子命令不可用。"
                     "请重装：pip install --force-reinstall \".[pdf]\"")
    else:
        lines.append(f"> ✅ 核心数据完整（fieldmate {info.get('fieldmate', '?')}）。"
                     "⚠️ 项只影响部分能力，按需安装可选依赖。")
    return "\n".join(lines)


def run(include_net: bool = False, as_json: bool = False) -> int:
    checks, info = collect_report(include_net=include_net)
    payload = {"ok": not any(c.fatal and not c.ok for c in checks),
               "checks": [c.as_dict() for c in checks], "info": info}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(report_markdown(checks, info))
    return EXIT_OK if payload["ok"] else EXIT_SOURCE
