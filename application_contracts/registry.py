"""Explicit backend application bindings and dependency validation — DR-071 §2."""
from threading import RLock

from .models import ContractError, LAYERS, copy_descriptor, identifier, require


def _strings(value):
    return (isinstance(value, list) and bool(value) and all(identifier(x) for x in value)
            and len(value) == len(set(value)))


def _validated(spec):
    valid = lambda condition: require(condition, 'invalid_contract')
    valid(isinstance(spec, dict))
    for key in ('application_id', 'contract_version', 'executor_id'):
        valid(identifier(spec.get(key)))
    valid(_strings(spec.get('entrypoints')) and _strings(spec.get('accepted_payload_kinds')))
    valid(spec.get('availability') in ('available', 'unavailable'))
    cardinality = spec.get('asset_cardinality')
    valid(isinstance(cardinality, dict))
    valid(type(cardinality.get('min')) is int and type(cardinality.get('max')) is int)
    valid(1 <= cardinality['min'] <= cardinality['max'])
    handlers = spec.get('repair_handlers')
    valid(isinstance(handlers, dict))
    valid(all(identifier(k) and callable(v) for k, v in handlers.items()))
    checkers = spec.get('checkers')
    valid(isinstance(checkers, list) and bool(checkers))
    owners = {}
    for index, checker in enumerate(checkers):
        valid(isinstance(checker, dict))
        valid(checker.get('layer') in LAYERS and type(checker.get('required')) is bool)
        valid(_strings(checker.get('required_codes')) and callable(checker.get('check')))
        deps = checker.get('depends_on')
        valid(isinstance(deps, list) and all(identifier(d) for d in deps) and len(deps) == len(set(deps)))
        for code in checker['required_codes']:
            valid(code not in owners)
            owners[code] = index
    valid({c['layer'] for c in checkers} == set(LAYERS))
    # Topological order permits forward declarations, rejects cycles and self-dependencies.
    remaining = list(range(len(checkers)))
    ordered = []
    available = set()
    for checker in checkers:
        valid(all(dep in owners for dep in checker['depends_on']))
    while remaining:
        ready = [i for i in remaining if set(checkers[i]['depends_on']) <= available]
        valid(bool(ready))
        for index in ready:
            ordered.append(checkers[index])
            available.update(checkers[index]['required_codes'])
            remaining.remove(index)
    result = copy_descriptor(spec)
    result['checkers'] = copy_descriptor(ordered)
    return result


class Registry:
    """Registration is atomic; returned descriptors never alias the stored configuration."""
    def __init__(self):
        self._applications = {}
        self._entrypoints = {}
        self._lock = RLock()

    def register(self, spec):
        fixed = _validated(spec)
        with self._lock:
            if (fixed['application_id'] in self._applications
                    or any(entry in self._entrypoints for entry in fixed['entrypoints'])):
                raise ContractError('registration_conflict', '应用或操作入口已注册。')
            self._applications[fixed['application_id']] = fixed
            for entry in fixed['entrypoints']:
                self._entrypoints[entry] = fixed['application_id']

    def resolve(self, entrypoint):
        with self._lock:
            if not identifier(entrypoint) or entrypoint not in self._entrypoints:
                raise ContractError('unknown_entrypoint', '未知应用操作入口。')
            return copy_descriptor(self._applications[self._entrypoints[entrypoint]])

    def descriptors(self):
        """Static public descriptors without callable/configuration internals — DR-071."""
        with self._lock:
            return [{k: copy_descriptor(spec[k]) for k in ('application_id', 'contract_version', 'entrypoints', 'availability')}
                    for spec in self._applications.values()]
