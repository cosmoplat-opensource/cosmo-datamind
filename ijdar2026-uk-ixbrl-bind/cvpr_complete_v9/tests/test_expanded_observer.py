from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from observe_expanded import bounded_words


class SharedObservationBudgetTests(unittest.TestCase):
    def test_over_budget_words_have_an_explicit_omission_count(self):
        words=[{'text':'abcdef','box':[0,0,1,1],'confidence':99} for _ in range(100)]
        kept,omitted=bounded_words(words,100)
        self.assertEqual(len(kept)+omitted,100);self.assertGreater(omitted,0)
        self.assertEqual(kept,bounded_words(words,100)[0])

    def test_only_visible_word_text_and_boxes_enter_prompt(self):
        kept,omitted=bounded_words([{'text':'million','box':[0,0,1,1],'confidence':95,'private_fact_id':'secret'}])
        self.assertEqual(set(kept[0]),{'text','box'});self.assertEqual(omitted,0)


if __name__=='__main__':unittest.main()
