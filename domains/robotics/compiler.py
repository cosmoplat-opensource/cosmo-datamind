"""Instantiate approved fixed templates using graph-derived paths — DR-062.

This low-level primitive does not confer business or robot execution authorization.
"""
from copy import deepcopy
from .xml_contract import _baseline_root, _config_locations
from .validator import validate_tree

def instantiate(template, config, *, models_root, config_paths, baseline=None, allow_unresolved=False):
    """Bind ontology configuration against an explicit ontology-built baseline."""
    baseline = _baseline_root(baseline)
    root = deepcopy(template)
    locations = _config_locations(root, config_paths)
    if not isinstance(config, dict) or set(config) != set(locations):
        raise ValueError("配置技能集合与本体公开接口不一致。")
    for skill, fields in locations.items():
        supplied = config[skill]
        if not isinstance(supplied, dict) or set(supplied) != set(fields):
            raise ValueError(f"{skill}: 配置参数与本体公开接口不一致。")
        for name, (node, port) in fields.items():
            raw = supplied[name]
            if not isinstance(raw, str) or not raw.strip() or any(c in raw for c in "{}\n\r\0"):
                raise ValueError(f"{skill}.{name}: expected a nonempty literal string, not a blackboard binding")
            node.set(port, raw)
    errors = validate_tree(root, models_root, baseline=baseline, config_paths=config_paths)
    if errors and not (allow_unresolved and all(item["code"] == "unresolved_parameter" for item in errors)):
        raise ValueError(errors)
    return root
