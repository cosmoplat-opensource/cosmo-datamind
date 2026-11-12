"""Shared values and strict JSON validation for application checks — DR-071."""
from copy import deepcopy
import hashlib
import json

LAYERS = ('schema', 'execution', 'task', 'environment')
STATUSES = ('passed', 'missing', 'unsupported', 'invalid', 'error', 'not_evaluated')


class ContractError(ValueError):
    """Public failures use fixed messages; never expose checker exception text."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code='invalid_request'):
    if not condition:
        raise ContractError(code, '应用契约定义无效。' if code == 'invalid_contract' else '应用检查输入不符合契约。')


def identifier(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 128


def revision(value):
    return type(value) is int and value >= 0


def canonical(value):
    """Only JSON values are accepted; NaN, objects and non-string keys are not fingerprints."""
    def validate(item):
        if isinstance(item, dict):
            require(all(isinstance(k, str) for k in item))
            for child in item.values():
                validate(child)
        elif isinstance(item, list):
            for child in item:
                validate(child)
        else:
            require(item is None or type(item) in (str, bool, int, float))
    try:
        validate(value)
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        raise ContractError('invalid_request', '应用检查输入不是有效 JSON。') from None


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def copy_descriptor(value):
    """Copy mutable configuration but preserve backend callable identity, without invoking it."""
    if callable(value):
        return value
    if isinstance(value, dict):
        return {key: copy_descriptor(child) for key, child in value.items()}
    if isinstance(value, list):
        return [copy_descriptor(child) for child in value]
    return deepcopy(value)
