#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""场景回归基准记分卡(DR-067):python3 scripts/scenario_bench.py

在临时目录里构造两个合成库,端到端跑体检、构建、复核、问数,逐项与标准答案比对。
不连模型、不碰真实数据;任何一项失败返回非零。
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    with tempfile.TemporaryDirectory(prefix="datamind-bench-") as tmp:
        os.environ.setdefault("DATAMIND_WORKDIR", str(Path(tmp) / "workdir"))
        (Path(tmp) / "workdir").mkdir(exist_ok=True)
        from tests.scenarios import bench
        checks = bench.run_all(tmp)
    width = max(len(c.id) for c in checks)
    for scenario in dict.fromkeys(c.scenario for c in checks):
        mine = [c for c in checks if c.scenario == scenario]
        print(f"\n{scenario}  {sum(c.ok for c in mine)}/{len(mine)}")
        for c in mine:
            print(f"  {'✓' if c.ok else '✗'} {c.id:<{width}}" + ("" if c.ok else f"  {c.detail}"))
    failed = sum(not c.ok for c in checks)
    print(f"\n合计 {len(checks) - failed}/{len(checks)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
