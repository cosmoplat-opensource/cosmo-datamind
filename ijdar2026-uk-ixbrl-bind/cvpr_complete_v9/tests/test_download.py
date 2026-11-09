from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from download import check_range


class PinnedDownloadTests(unittest.TestCase):
    def test_ignored_or_shifted_range_is_not_appended_to_partial_artifact(self):
        for status,header,size in [(200,'bytes 8-15/32',8),(206,'bytes 0-7/32',8),(206,'bytes 8-15/32',7)]:
            with self.assertRaises(ValueError):check_range(status,header,8,16,32,size)
        check_range(206,'bytes 8-15/32',8,16,32,8)


if __name__=='__main__':unittest.main()
