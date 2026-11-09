from pathlib import Path
import sys

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from native_joint_features_v18 import node_beam, unit_compatibility


def test_beam_is_reference_blind_distinct_and_retains_unknown():
    rows = [{"value":{"kind":"instant","instant":"2022-12-31"}},
            {"value":{"kind":"instant","instant":"2022-12-31"}},
            {"value":{"kind":"duration","start_date":"2022-01-01","end_date":"2022-12-31"}}]
    chosen = node_beam(rows,np.array([2.,1.,0.,-.75]),"period")
    assert chosen == [0,2,3]


def test_public_field_unit_types_supply_a_relation_not_guessed_currency():
    field = {"metadata":{"type":"{http://www.xbrl.org/2003/instance}monetaryItemType"}}
    amount = {"value":{"unit":{"numerator":["{http://www.xbrl.org/2003/iso4217}GBP"],"denominator":[]}}}
    assert unit_compatibility(field,amount) == (1.,1.)
    amount["value"]["unit"]["denominator"] = ["{http://www.xbrl.org/2003/instance}shares"]
    assert unit_compatibility(field,amount) == (1.,0.)
    field["metadata"]["type"] = "{urn:issuer}CustomType"
    assert unit_compatibility(field,amount) == (0.,0.)
