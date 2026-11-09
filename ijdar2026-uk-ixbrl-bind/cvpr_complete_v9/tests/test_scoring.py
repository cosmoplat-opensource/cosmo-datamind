from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from score_native import verify_predictions, summarize, load_metric
from score_scope import year_compatible, correct_quantity


class ScoringTests(unittest.TestCase):
    def test_duplicate_predictions_cannot_hide_missing_question(self):
        with self.assertRaises(RuntimeError):
            verify_predictions([{'task_id': 'a'}, {'task_id': 'b'}],
                [{'task_id': 'a'}, {'task_id': 'a'}], {'expected_tasks': 2, 'complete': True})

    def test_format_failure_stays_in_denominator(self):
        result = summarize([{'em': 1., 'f1': 1., 'scale_matches': True},
                            {'em': 0., 'f1': 0., 'scale_matches': False}])
        self.assertEqual(result['em'], .5)

    def test_only_the_original_explicit_date_criterion_is_used(self):
        p = {'period_support': {'year': '2023'}}
        self.assertFalse(year_compatible('What was revenue?', p))
        self.assertFalse(year_compatible('Compare 2023 and 2022 revenue', p))
        self.assertTrue(year_compatible('Revenue on 2023-12-31?', p))

    def test_matching_number_with_wrong_unit_is_incorrect(self):
        p = {'computed_answer': '10', 'unit': 'GBP'}
        g = {'answer_scorable': True, 'canonical_values': ['10'], 'canonical_unit': 'USD'}
        self.assertFalse(correct_quantity(p, g))

    def test_official_metric_handles_spans_and_invalid_output(self):
        score, _ = load_metric()
        annotation = {'answer_type': 'multi-span', 'answer': ['Revenue', 'Expenses'], 'scale': ''}
        self.assertEqual(score(annotation, {'answer': ['Expenses', 'Revenue'], 'scale': ''})['em'], 1.)
        self.assertEqual(score(annotation, None)['em'], 0.)


if __name__ == '__main__':
    unittest.main()
