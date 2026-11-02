"""Pin fixed-template and public-input semantics — DR-062 / ROB-08 (T02 structural subset)."""
from copy import deepcopy

import pytest

from tests.robotics_support import api, config, prepared, sequence, signature, trees


def instantiate(template, values, models, paths, baseline):
    return api("compiler").instantiate(template, values, models_root=models,
                                       config_paths=paths, baseline=baseline)


def test_only_public_configuration_changes_in_fixed_template():
    template, models, paths = prepared()
    original = deepcopy(template)
    defaults = config()
    unchanged = instantiate(template, defaults, models, paths, original)
    assert trees(unchanged) == trees(original)
    changed = deepcopy(defaults)
    changed["Perception"].update(position="[1,2,3,4,5,6]", exposure_time="155",
                                 object_name="part_A", model_name="model_A", threshold="0.72")
    changed["Place"]["place_pose"] = "[200,-900,500,1,0,0,0]"
    candidate = instantiate(template, changed, models, paths, original)
    assert trees(template) == trees(original), "Compiler mutated its input template"
    assert len(candidate.findall("BehaviorTree")) == 4
    for name in ("Perception", "grasp", "Place"):
        assert signature(sequence(candidate, name)) == signature(sequence(original, name))
    before, after = sequence(original, "main"), sequence(candidate, "main")
    assert len(before) == len(after) == 10
    assert [n.tag for n in before] == [n.tag for n in after]
    actual = {(i, key): new.get(key) for i, (old, new) in enumerate(zip(before, after), 1)
              for key in set(old.attrib) | set(new.attrib) if old.get(key) != new.get(key)}
    assert actual == {
        (2, "value"): changed["Perception"]["position"],
        (5, "exposure_time"): "155", (5, "object_name"): "part_A",
        (5, "model_name"): "model_A", (5, "threshold"): "0.72",
        (9, "place_pose"): changed["Place"]["place_pose"],
    }
    assert not api("validator").validate_tree(candidate, models, baseline=original, config_paths=paths)


@pytest.mark.parametrize("change", ["reorder", "fixed_value", "data_reference"])
def test_reordering_requests_are_not_silently_accepted(change):
    template, models, paths = prepared()
    broken = deepcopy(template)
    seq = sequence(broken, "grasp")
    if change == "reorder":
        first = seq[0]
        seq.remove(first)
        seq.append(first)
    elif change == "fixed_value":
        seq[1].set("offset", "[0,0,-55,1,0,0,0]")
    else:
        seq[2].set("target", "{missing}")
    errors = api("validator").validate_tree(broken, models, baseline=template, config_paths=paths)
    assert errors, change
    with pytest.raises(ValueError):
        instantiate(broken, config(), models, paths, template)


@pytest.mark.parametrize("section,key,value", [
    ("Perception", "threshold", "NaN"),
    ("Perception", "position", "[0,1]"),
    ("Perception", "exposure_time", "-1"),
    ("Perception", "object_name", "{invented}"),
    ("Place", "place_pose", "[1,2,3]"),
    ("grasp", "robot_ref", "another_robot"),
])
def test_illegal_or_nonpublic_configuration_is_rejected(section, key, value):
    template, models, paths = prepared()
    values = config()
    values[section][key] = value
    with pytest.raises(ValueError):
        instantiate(template, values, models, paths, template)
