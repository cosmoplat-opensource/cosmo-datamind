import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
spec=importlib.util.spec_from_file_location('v9_observe',ROOT/'observe.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class ObservationTests(unittest.TestCase):
    def test_outside_image_header_is_rejected(self):
        with self.assertRaises(ValueError):m.validate(json.dumps({'headers':[{'text':'2024','kind':'period','box':[0,0,2,1]}]}))
    def test_unknown_headers_do_not_require_fabricated_evidence(self):
        result=m.validate('{"headers":[],"unresolved":["Unit scale is unreadable"]}')
        self.assertEqual(result['headers'],[]);self.assertTrue(result['unresolved'])
    def test_complete_over_budget_reply_retains_a_bounded_prefix(self):
        headers=[{'text':str(i),'kind':'row','box':[0,0,.5,.5]} for i in range(45)]
        result=m.validate(json.dumps({'headers':headers}))
        self.assertEqual(len(result['headers']),40);self.assertEqual(result['omitted_header_count'],5)


if __name__=='__main__':unittest.main()
