#!/usr/bin/env python3
"""集成断言骨架。每个端点 happy path + 边界 + 安全。"""
import sys

P = F = 0
fails = []


def chk(name, cond, detail=""):
    global P, F
    if cond:
        P += 1
    else:
        F += 1
        fails.append(f"{name}: {detail}")
    print(f"  {'✓' if cond else '✗ FAIL'} {name}")


# 在这里加你的端点测试
# def test_xxx():
#     chk("xxx 端点 happy path", True)


if __name__ == "__main__":
    print(f"\n集成测试：{P} 通过 / {F} 失败")
    sys.exit(1 if F else 0)
