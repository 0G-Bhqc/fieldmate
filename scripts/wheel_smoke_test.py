#!/usr/bin/env python
"""wheel 冒烟测试 —— 「pip install 后立刻可用」这条生死线的验收脚本。

流程（全部可复现，不依赖仓库源码树）：
1. `pip wheel .` 构建 wheel；
2. 建一个干净 venv，把 wheel 装进去（非 editable —— 这正是以前必崩的用法）；
3. 在**与仓库无关的临时目录**里跑核心子命令，逐个断言退出码：
   - list / patterns               加载包内缺陷库与检测规则
   - prereg --init + 自校验        实验核验的最小闭环
   - coverage --corpus <包内manifest>   manifest 装载 + 缓存解析 + 退出码语义
4. 任何一步失败即非零退出并打印现场。

用法：python scripts/wheel_smoke_test.py
（CI 里与 pytest 并列执行；本地改动打包配置后也应跑一遍。）
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import venv
from pathlib import Path

# 这个脚本自己在 Windows CI 上就踩过 cp1252 崩溃（print 中文即
# UnicodeEncodeError）—— 与 fieldmate.cli.main 同一款防护，脚本自保。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "build" / "wheelhouse"


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", **kw)
    if r.returncode != 0:
        tail = (r.stdout + r.stderr)[-1500:]
        raise SystemExit(f"[smoke] 命令失败（exit {r.returncode}）："
                         f"{' '.join(map(str, cmd))}\n{tail}")
    return r


def main() -> int:
    print("[smoke] 1/4 构建 wheel …")
    DIST.mkdir(parents=True, exist_ok=True)
    run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps",
         "--wheel-dir", str(DIST)], cwd=str(ROOT))
    wheels = sorted(DIST.glob("fieldmate-*.whl"))
    assert wheels, "没有产出 fieldmate wheel"
    whl = wheels[-1]
    print(f"[smoke]    {whl.name}")

    with tempfile.TemporaryDirectory(prefix="fm-smoke-") as td:
        td = Path(td)
        print("[smoke] 2/4 干净 venv 安装（非 editable）…")
        venv.create(td / "venv", with_pip=True)
        py = td / "venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        run([str(py), "-m", "pip", "install", "--quiet", str(whl)])

        # 工作目录 = 临时目录：宿主 harness 从任意 cwd 调 CLI 的真实场景
        workdir = td / "work"
        workdir.mkdir()
        def fm(*args: str, expect: int) -> str:
            # 子进程（fieldmate）保证 stdout 是 UTF-8（cli.main 强制）；
            # 父侧必须显式按 UTF-8 解码 —— text=True 默认用 locale（gbk/cp1252），
            # 解码线程会炸死并让 r.stdout 静默变 None（Windows CI 实测）。
            r = subprocess.run([str(py), "-m", "fieldmate", *args],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", cwd=str(workdir))
            if r.returncode != expect:
                raise SystemExit(
                    f"[smoke] fieldmate {' '.join(args)} 退出码 {r.returncode}，预期 {expect}\n"
                    f"{(r.stdout + r.stderr)[-1500:]}")
            return r.stdout

        print("[smoke] 3/4 核心子命令（在无关 cwd）…")
        list_out = fm("list", expect=0)
        assert "缺陷库" in list_out, "list 未输出缺陷库统计"
        fm("patterns", "--json", expect=0)
        fm("doctor", expect=0)
        print("[smoke]    list / patterns / doctor ✓（包内数据可加载）")

        print("[smoke] 4/4 prereg 闭环 + 语料 manifest 装载 …")
        prereg = workdir / "smoke-exp.json"
        fm("prereg", "--init", "--out", str(prereg), expect=0)
        assert prereg.exists(), "prereg --init 未产出文件"
        fm("prereg", "--prereg", str(prereg), expect=0)
        print("[smoke]    prereg --init / 自校验 ✓")

        # 用冒烟 venv 里的 python 定位包内 manifest（wheel 是否真带上了 package-data）。
        # 必须 cwd=workdir：python -c 会把当前目录加进 sys.path，若继承仓库根，
        # files('fieldmate') 会解析到源码树而非 wheel 安装副本 —— 校验就成了假阳性
        # （实测还顺带在 Windows 上崩了：源码路径含中文，child 的 cp1252 stdout 编不出）。
        loc = run([str(py), "-c",
                   "from importlib import resources; import sys;"
                   "p = resources.files('fieldmate') / 'libraries' / 'corpus_bulk.json';"
                   "print(p); sys.exit(0 if p.is_file() else 1)"],
                  cwd=str(workdir))
        manifest = loc.stdout.strip()
        assert Path(manifest).is_file(), "包内 corpus_bulk.json 缺失（package-data 漏配）"
        loc2 = run([str(py), "-c",
                    "from importlib import resources; import sys;"
                    "p = resources.files('fieldmate') / 'prompts' / 'assumption_refine.md';"
                    "print(p); sys.exit(0 if p.is_file() else 1)"],
                   cwd=str(workdir))
        assert Path(loc2.stdout.strip()).is_file(), "包内 prompts 缺失（package-data 漏配）"
        # coverage 对无 PDF 缓存的 manifest 会诚实退出 3（语料未覆盖），不算失败
        cov = subprocess.run([str(py), "-m", "fieldmate", "coverage", "--corpus", manifest],
                             capture_output=True, text=True,
                             encoding="utf-8", errors="replace", cwd=str(workdir))
        if cov.returncode not in (0, 3):
            raise SystemExit(f"[smoke] coverage 退出码 {cov.returncode}（预期 0/3）\n"
                             f"{(cov.stdout + cov.stderr)[-1500:]}")
        assert "篇有全文" in cov.stdout, "coverage 未输出统计"
        print(f"[smoke]    coverage --corpus ✓（退出码 {cov.returncode}，manifest 装载正常）")

    print("[smoke] ✓ 全部通过：pip install 后立即可用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
