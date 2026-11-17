#!/usr/bin/env bash
# 多通道交叉复核(只读)。参数原样传给 scripts/cross_check.py,用法见 SKILL.md。
set -euo pipefail
exec python3 "$(cd "$(dirname "$0")" && pwd)/scripts/cross_check.py" "$@"
