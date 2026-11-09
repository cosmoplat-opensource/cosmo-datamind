from pathlib import Path
import sys
import unittest
import torch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scope_model import ScopeModel,METHODS,latent_answer_loss,partial_scope_loss,retained_scope_sets


class ScopeMechanismTests(unittest.TestCase):
    def test_unknown_ownership_does_not_provide_fake_supervision(self):
        logits=torch.tensor([[1.,-2.,.5]],requires_grad=True)
        loss=partial_scope_loss(logits,torch.full_like(logits,float('nan')));loss.backward()
        self.assertEqual(loss.item(),0);self.assertEqual(logits.grad.abs().sum().item(),0)
    def test_answer_collision_keeps_all_compatible_programs(self):
        s=torch.zeros(3,requires_grad=True);mask=torch.tensor([True,True,True])
        loss=latent_answer_loss(s,mask,torch.tensor([True,True,False]));loss.backward()
        self.assertAlmostEqual(s.grad[0].item(),s.grad[1].item())
        self.assertLess(s.grad[0].item(),0);self.assertGreater(s.grad[2].item(),0)
    def test_disconnected_scope_and_local_exception_are_representable(self):
        self.assertEqual(retained_scope_sets(torch.tensor([[10.,-10.,10.]]),.5),[[0,2]])
    def test_all_controls_have_same_inputs_and_parameter_budget(self):
        counts=[]
        for method in METHODS:
            torch.manual_seed(19);model=ScopeModel(method,12)
            counts.append(sum(p.numel() for p in model.parameters()))
            out=model(torch.randn(6,12),torch.tensor([0,1]),torch.tensor([2,3,4,5]),
                      torch.tensor([0,1]),torch.randn(12),torch.tensor([[0,0,1,-1,-1,-1],[2,1,0,-1,-1,-1]]))
            self.assertEqual(tuple(out['scope_logits'].shape),(2,4))
            self.assertTrue(torch.isfinite(out['scores']).all())
            self.assertTrue(torch.allclose(out['scores'],torch.zeros(2),atol=1e-6))
        self.assertLess(max(counts),1000000)
    def test_no_answer_compatible_candidate_has_no_gradient_label(self):
        s=torch.randn(4,requires_grad=True)
        loss=latent_answer_loss(s,torch.ones(4,dtype=torch.bool),torch.zeros(4,dtype=torch.bool));loss.backward()
        self.assertEqual(s.grad.abs().sum().item(),0)


if __name__=='__main__':unittest.main()
