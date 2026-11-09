from pathlib import Path
import sys
import unittest
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pilot_features import pool


class PatchAlignmentTests(unittest.TestCase):
    def test_subpatch_glyph_keeps_actual_overlapping_visual_feature(self):
        x=np.zeros((197,768),dtype='float32');x[1]=5
        np.testing.assert_array_equal(pool(x,[0,0,.001,.001]),np.full(768,5))
    def test_unknown_geometry_is_not_claimed_as_image_evidence(self):
        np.testing.assert_array_equal(pool(np.ones((197,768)),None),np.zeros(768))
        with self.assertRaises(ValueError):pool(np.ones((197,768)),[0,0,2,1])


if __name__=='__main__':unittest.main()
