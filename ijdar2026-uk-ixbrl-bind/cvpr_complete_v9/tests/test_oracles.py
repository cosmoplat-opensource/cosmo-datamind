from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from audit_oracles import canonical_header,valid_box,target_words


class ScopeQualificationTests(unittest.TestCase):
    def test_malformed_scope_is_unknown_not_a_teacher_label(self):
        result=canonical_header({'text':'GBP million','kind':'unit','box':[0,0,.5,.1],
                 'applies_to':[[0,0,200,200]],'ambiguous':False})
        self.assertEqual(result['possible_scope_regions'],[])
        self.assertTrue(result['ambiguous']);self.assertFalse(result['independent_ownership_label'])
    def test_nan_or_degenerate_scope_cannot_be_executed(self):
        self.assertFalse(valid_box([0,0,float('nan'),1]));self.assertFalse(valid_box([0,0,0,1]))
    def test_target_matching_uses_image_geometry_not_answer_value(self):
        words=[{'text':'6','box':[.1,.1,.2,.2]},{'text':'6','box':[.8,.8,.9,.9]}]
        self.assertEqual(target_words(words,[.1,.1,.2,.2]),words[:1])


if __name__=='__main__':unittest.main()
