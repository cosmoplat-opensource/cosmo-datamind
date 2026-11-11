"""Canonical identities and public modeling failures — DR-070."""
from copy import deepcopy
import hashlib
import json


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class ModelingError(ValueError):
    def __init__(self, code, message, *, issues=None):
        super().__init__(message)
        self.code = code
        self.issues = deepcopy(issues or [])


def checked_tbox(value):
    from ._schema import load_tbox
    if not isinstance(value, dict):
        raise ModelingError('invalid_schema', '需要已确认的 TBox 骨架。')
    return load_tbox(canonical({'namespace': value.get('namespace'), 'result': value}).encode())
