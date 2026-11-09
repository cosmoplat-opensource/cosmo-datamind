from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evidence_program import calculate, execute, scope_warnings


def program():
    scope = {'row': 'Revenue', 'period': '2023', 'unit': 'GBP', 'scale': 'million',
             'entity': 'Group', 'dimension': 'unknown', 'version': 'unknown'}
    return {'evidence': [{'id': 'e0', 'page': 0, 'box': [.1, .1, .2, .2], 'text': '120', 'value': '120', 'scope': scope},
        {'id': 'e1', 'page': 0, 'box': [.2, .1, .3, .2], 'text': '100', 'value': '100', 'scope': {**scope, 'period': '2022'}}],
        'operation': 'arithmetic', 'expression': '(e0-e1)/e1*100', 'answer_refs': [], 'scale': 'percent'}


class ProgramTests(unittest.TestCase):
    def test_arithmetic_executes_grounded_numbers(self):
        r = execute(program(), 1)
        self.assertEqual(r['answer'], {'answer': '20.0', 'scale': 'percent'})
        self.assertFalse(r['visual_grounding_independently_certified'])

    def test_arbitrary_code_and_ungrounded_constants_rejected(self):
        from decimal import Decimal
        for expression in ('__import__("os").system("id")', 'e0+7', 'e0**100', 'e0[0]', '100'):
            with self.assertRaises(ValueError): calculate(expression, {'e0': Decimal(1)})

    def test_coordinates_and_duplicate_evidence_rejected(self):
        p = program(); p['evidence'][0]['box'][2] = 1.2
        with self.assertRaises(ValueError): execute(p, 1)
        p = program(); p['evidence'][1]['id'] = 'e0'
        with self.assertRaises(ValueError): execute(p, 1)

    def test_scope_difference_is_a_warning_not_automatic_rejection(self):
        p = program(); p['evidence'][1]['scope']['unit'] = 'USD'
        self.assertTrue(any('unit' in x for x in scope_warnings(p)))
        self.assertEqual(execute(p, 1)['answer']['answer'], '20.0')

    def test_extract_preserves_printed_span(self):
        p = program(); p.update(operation='extract', expression='', answer_refs=['e0'])
        self.assertEqual(execute(p, 1)['answer']['answer'], ['120'])

    def test_division_by_zero_is_returned_as_execution_error(self):
        p = program(); p['evidence'][1]['value'] = '0'
        with self.assertRaises(ValueError): execute(p, 1)

    def test_count_can_use_nonnumeric_visible_spans(self):
        p = program(); p['evidence'][0]['value'] = None; p['evidence'][1]['value'] = None
        p['expression'] = 'count(e0,e1)'
        self.assertEqual(execute(p, 1)['answer']['answer'], '2')

    def test_mean_uses_evidence_count_without_invented_denominator(self):
        p = program(); p['expression'] = 'mean(e0,e1)'
        self.assertEqual(execute(p, 1)['answer']['answer'], '110')

    def test_finite_json_numbers_and_decimal_strings_are_equivalent(self):
        p = program(); p['evidence'][0]['value'] = 120; p['evidence'][1]['value'] = 100.0
        self.assertEqual(execute(p, 1)['answer']['answer'], '20.0')
        p['evidence'][0]['value'] = float('nan')
        with self.assertRaises(ValueError): execute(p, 1)


if __name__ == '__main__': unittest.main()
