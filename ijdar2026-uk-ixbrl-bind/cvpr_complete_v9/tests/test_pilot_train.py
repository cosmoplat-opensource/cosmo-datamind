from pathlib import Path
import sys
import unittest
import torch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from pilot_train import answer_masks,average_paths,answer_bag_objective,balanced_visual_block


class AnswerBagTrainingTests(unittest.TestCase):
    def test_extra_identical_support_paths_do_not_become_extra_votes(self):
        values=average_paths(torch.tensor([2.,2.,2.,3.]),torch.tensor([0,0,0,1]),2)
        self.assertTrue(torch.allclose(values,torch.tensor([2.,3.])))
    def test_feedback_keeps_equal_answers_at_different_regions_latent(self):
        item={'candidates':[{'computed_answer':'4','unit':'GBP'},{'computed_answer':'4','unit':'iso4217:GBP'},
                            {'computed_answer':'4','unit':'USD'}]}
        _,positive=answer_masks(item,{'period_status':['unknown','supported','supported']},
              {'answer_in_base_units':'4','answer_unit':'iso4217:GBP'})
        self.assertEqual(positive.tolist(),[True,True,False])

    def test_null_is_anchored_in_the_same_denominator_used_at_inference(self):
        s=torch.tensor([1.,0.]);eligible=torch.tensor([True,True]);positive=torch.tensor([True,False])
        unanchored=answer_bag_objective(s,eligible,positive)
        self.assertAlmostEqual(float(unanchored),float(answer_bag_objective(s-10,eligible,positive)),places=5)
        self.assertGreater(float(answer_bag_objective(s-10,eligible,positive,True)),
                           float(answer_bag_objective(s,eligible,positive,True))+5)

    def test_missing_candidate_does_not_create_a_false_null_label(self):
        s=torch.tensor([1.,0.],requires_grad=True)
        loss=answer_bag_objective(s,torch.tensor([True,True]),torch.tensor([False,False]),True)
        loss.backward();self.assertTrue(torch.equal(s.grad,torch.zeros(2)))

    def test_shared_visual_scaling_keeps_unknowns_and_text_unchanged(self):
        x=torch.zeros(2,1159);x[0,:768]=20;x[:,768:]=torch.randn(2,391)
        y=balanced_visual_block(x)
        self.assertTrue(torch.equal(x[:,768:],y[:,768:]))
        self.assertEqual(float(y[1,:768].norm()),0.)
        self.assertAlmostEqual(float(y[0,:768].norm()),1.,places=5)


if __name__=='__main__':unittest.main()
