from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from native_baseline import validate_answer


class TerminalAnswerTests(unittest.TestCase):
    def test_truncation_is_a_failure_even_if_prefix_looks_like_answer(self):
        with self.assertRaises(ValueError):validate_answer('{"answer":5,"scale":"million"}','length')
    def test_unknown_answer_can_be_returned_without_fabricating_zero(self):
        self.assertEqual(validate_answer('{"answer":"unanswerable","scale":""}','stop')['answer'],'unanswerable')
    def test_nonfinite_and_extra_model_commentary_are_not_scored(self):
        for raw in ('{"answer":NaN,"scale":""}','{"answer":5,"scale":"","comment":"thought"}'):
            with self.assertRaises(ValueError):validate_answer(raw,'stop')


if __name__=='__main__':unittest.main()
