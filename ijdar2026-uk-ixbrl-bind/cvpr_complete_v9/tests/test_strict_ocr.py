from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from strict_native_ocr import parse_tsv


HEAD = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"


class LiteralTSVTests(unittest.TestCase):
    def test_literal_open_quote_does_not_swallow_next_numeric_row(self):
        raw = HEAD + '5\t1\t1\t1\t1\t1\t1\t2\t3\t4\t90\t"\n'
        raw += "5\t1\t1\t1\t1\t2\t10\t2\t30\t4\t91\t3,142\n"
        words = parse_tsv(raw, 100, 100)
        self.assertEqual([w["text"] for w in words], ['"', "3,142"])
        self.assertFalse(any("\t" in w["text"] or "\n" in w["text"] for w in words))

    def test_visual_quoted_text_keeps_its_actual_quotes(self):
        raw = HEAD + '5\t1\t1\t1\t1\t1\t1\t2\t30\t4\t90\t"restated"\n'
        self.assertEqual(parse_tsv(raw, 100, 100)[0]["text"], '"restated"')

    def test_nonfinite_confidence_and_bad_geometry_fail_explicitly(self):
        for confidence, x in (("nan", "1"), ("90", "99")):
            raw = HEAD + f"5\t1\t1\t1\t1\t1\t{x}\t2\t3\t4\t{confidence}\t1\n"
            with self.assertRaises(ValueError):
                parse_tsv(raw, 100, 100)

    def test_malformed_tsv_is_not_silently_truncated(self):
        with self.assertRaises(ValueError):
            parse_tsv(HEAD + "5\t1\t1\n", 100, 100)


if __name__ == "__main__":
    unittest.main()
