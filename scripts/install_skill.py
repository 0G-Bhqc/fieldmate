#!/usr/bin/env python
"""把 fieldmate skill 挂载到一个工作区 —— 「直接挂载到任意 Harness」的安装器。

ZCode 在 workspace 作用域扫描 `<workspace>/.zcode/skills/<name>/SKILL.md`，
Claude Code 同理扫描 `<workspace>/.claude/skills/`。本脚本把插件源里的
SKILL.md 复制到目标工作区的对应目录，新会话即自动发现、无需任何 UI 操作。

子 Agent 的正式注册（agents/ 组件）依赖插件安装（市场 → 安装），
skill 挂载是零 UI 的最小可用形态：宿主按 SKILL.md 剧本行事。

用法：
    python scripts/install_skill.py                  # 挂载到当前目录
    python scripts/install_skill.py --workspace X    # 挂载到指定目录
    python scripts/install_skill.py --host claude    # 用 .claude/ 而不是 .zcode/
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

# Windows 上 stdout 默认 locale 编码（gbk/cp1252），中文 print 会崩——
# 与 fieldmate.cli.main / wheel_smoke_test 同款入口防护（CI 已两次抓到同类）。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

ROOT = Path(__file__).resolve().parents[1]
SKILL_SRC = ROOT / "plugins" / "fieldmate" / "skills" / "fieldmate" / "SKILL.md"

HOST_DIRS = {
    "agents": ".agents",
    "antigravity": ".agents",
    "claude": ".claude",
    "codex": ".codex",
    "cursor": ".cursor",
    "zcode": ".zcode",
}


def main() -> int:
    ap = argparse.ArgumentParser(description="挂载 fieldmate skill 到各种 Agent Harness 工作区")
    ap.add_argument("--workspace", default=".", help="目标工作区目录（默认当前目录）")
    ap.add_argument("--host", choices=sorted(HOST_DIRS), default="zcode",
                    help="宿主 Harness 类型（默认 zcode）")
    ap.add_argument("--global", dest="is_global", action="store_true",
                    help="挂载到当前用户全局 Agent Harness 配置目录")
    args = ap.parse_args()

    def fail(msg: str) -> int:
        print(f"[install] ⛔ {msg}", file=sys.stderr)
        return 2

    ws = Path(args.workspace).resolve()
    if not ws.is_dir():
        return fail(f"工作区不存在：{ws}")
    if not SKILL_SRC.is_file():
        return fail(f"找不到插件源里的 SKILL.md：{SKILL_SRC}（安装不完整？）")
    text = SKILL_SRC.read_text(encoding="utf-8")
    if not (text.startswith("---") and "name: fieldmate" in text.split("---")[1]):
        return fail("SKILL.md frontmatter 不符（name: fieldmate）")

    if args.is_global:
        if args.host in ("antigravity", "agents"):
            dst_dir = Path.home() / ".gemini" / "config" / "skills" / "fieldmate"
        elif args.host == "claude":
            dst_dir = Path.home() / ".claude" / "skills" / "fieldmate"
        else:
            dst_dir = Path.home() / HOST_DIRS[args.host] / "skills" / "fieldmate"
    else:
        dst_dir = ws / HOST_DIRS[args.host] / "skills" / "fieldmate"
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / "SKILL.md"
    changed = (not dst.exists()) or dst.read_text(encoding="utf-8") != text
    if changed:
        shutil.copyfile(SKILL_SRC, dst)
    print(f"[install] {'已写入' if changed else '已是最新'}：{dst}")
    print("[install] 新会话/新任务即自动发现（当前会话不热加载）。")
    print(f"[install] 卸载：删除 {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
