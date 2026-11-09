from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qualify_teacher_cells import public_number_candidates


class IndependentPublicParseTests(unittest.TestCase):
    def test_comma_locale_ambiguity_survives_reference_independently(self):
        self.assertEqual(set(public_number_candidates('1,234')),{'1234','1.234'})

    def test_literal_parentheses_supply_a_negative_sign(self):
        self.assertEqual(public_number_candidates('(13.8)p'),['-13.8'])
        self.assertEqual(public_number_candidates('13.8'),['13.8'])

    def test_dash_placeholder_is_not_forced_to_zero(self):
        for word in ('-','—','N/A','nil'):
            self.assertEqual(public_number_candidates(word),[])

    def test_label_digits_are_not_invented_numeric_values(self):
        self.assertEqual(public_number_candidates('2023 Revenue'),[])


if __name__=='__main__':unittest.main()
