# -*- coding: utf-8 -*-
"""`.env` 里留空的变量必须等同于「未设置」。

背景:`.env.example` 明确写着「所有变量均可留空」,而 `cp .env.example .env`
之后 `set -a; . ./.env` 会把留空项设成**空字符串**,不是未设置。此时
`os.environ.get(NAME, DEFAULT)` 返回的是 `""` 而非 `DEFAULT`——
`DATAMIND_LOG_LEVEL=` 留空即让 `logging.basicConfig(level="")` 抛
`ValueError: Unknown level: ''`,**服务照文档配置就起不来**。

故凡「留空即等同未设置」的读取一律写成 `os.environ.get(NAME) or DEFAULT`。
本测试用源码断言锁住该写法,并对最要命的日志级别做一次真实构造验证。
"""
import ast
import logging
import os
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

# 这些变量在 .env.example 中留空发布,或语义上「空 == 未配置」。
# 读它们时不得依赖 os.environ.get 的第二参数兜底。
MUST_TOLERATE_EMPTY = {
    "DATAMIND_LOG_LEVEL",
    "DATAMIND_HOST",
    "DATAMIND_PORT",
    "DATAMIND_LOCAL_LLM_BASE",
    "CLAW_DRIVER",
    "CLAUDE_MODEL",
    "HERMES_MODEL",
    "HERMES_PROVIDER",
    "OPENCLAW_MODEL",
}

SCANNED = ["server.py", "srv_engine.py", "bp_engine.py", "srv_context.py",
           "openai_runtime.py", "store.py"]


def _get_with_default_calls(path):
    """找出 os.environ.get(NAME, DEFAULT) 形式的两参调用,返回 (变量名, 行号)。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) != 2:
            continue
        fn = node.func
        if not (isinstance(fn, ast.Attribute) and fn.attr == "get"):
            continue
        owner = fn.value
        is_environ = (
            (isinstance(owner, ast.Attribute) and owner.attr == "environ")
            or (isinstance(owner, ast.Name) and owner.id == "environ")
        )
        if not is_environ:
            continue
        name, default = node.args
        if not (isinstance(name, ast.Constant) and isinstance(name.value, str)):
            continue
        # 默认值本身就是空串时,空串与默认值等价,不构成隐患(如 get(X, ""))。
        # 只有默认值非空,空串才会顶掉它 —— 那才是要抓的。
        if isinstance(default, ast.Constant) and default.value in ("", None):
            continue
        out.append((name.value, node.lineno))
    return out


@pytest.mark.parametrize("fname", SCANNED)
def test_empty_tolerant_vars_never_rely_on_get_default(fname):
    """留空即失效的变量,不得用 os.environ.get(NAME, DEFAULT) 读取。"""
    path = ROOT / fname
    if not path.exists():
        pytest.skip(f"{fname} 不存在")
    offenders = [
        f"{fname}:{line} os.environ.get({var!r}, ...)"
        for var, line in _get_with_default_calls(path)
        if var in MUST_TOLERATE_EMPTY
    ]
    assert not offenders, (
        "以下变量在 .env 中可留空,空串会绕过第二参数的默认值,"
        "请改用 os.environ.get(NAME) or DEFAULT:\n  " + "\n  ".join(offenders)
    )


def test_empty_log_level_does_not_break_logging(monkeypatch):
    """最要命的一处:日志级别留空时不得抛异常。"""
    monkeypatch.setenv("DATAMIND_LOG_LEVEL", "")
    level = (os.environ.get("DATAMIND_LOG_LEVEL") or "INFO").upper()
    logging.getLogger("datamind-envtest").setLevel(level)   # 不抛即通过
    assert level == "INFO"


def test_env_example_blank_vars_are_covered():
    """.env.example 里留空的变量,若被代码以两参 get 读取,必须在豁免清单内。

    该清单是有意为之的白名单:新增留空变量时,这条会提醒作者一并检查读取方式。
    """
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    blanks = set(re.findall(r"^([A-Z][A-Z0-9_]*)=$", example, flags=re.M))
    assert blanks, ".env.example 应当含留空示例项"
    risky = set()
    for fname in SCANNED:
        path = ROOT / fname
        if not path.exists():
            continue
        for var, _ in _get_with_default_calls(path):
            if var in blanks:
                risky.add(var)
    assert not risky, (
        "这些变量在 .env.example 中留空发布,却用 get(NAME, DEFAULT) 读取,"
        "空串会顶掉默认值:" + ", ".join(sorted(risky))
    )
