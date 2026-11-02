#!/usr/bin/env python3
"""全量回归入口。

用法：python scripts/check.py --suite all
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(name, cmd):
    print(f"\n=== {name} ===")
    return subprocess.call(cmd, cwd=ROOT)


def main():
    failures = 0
    failures += run("lint", [sys.executable, "-m", "ruff", "check", "."])
    failures += run("unit", [sys.executable, "-m", "pytest", "tests/", "-v"])
    # 按需加：集成测试、UI 测试、覆盖率
    if failures:
        print(f"\n❌ {failures} 项失败")
        sys.exit(1)
    print("\n✅ 全部通过")


if __name__ == "__main__":
    main()
