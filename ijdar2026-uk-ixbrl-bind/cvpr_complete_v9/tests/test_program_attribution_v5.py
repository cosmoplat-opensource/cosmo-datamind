import sys
import unittest
from pathlib import Path

import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from program_attribution_v5 import NEW_METHODS, make, program_properties


class ProgramAttributionTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(1)

    def test_public_program_properties_distinguish_literal_sign_orientation_and_null(
        self,
    ):
        candidates = torch.zeros(5, 12)
        candidates[:4, 4] = 1
        candidates[:4, 0] = torch.tensor([1, 0, 1, 0])
        candidates[:4, 1] = torch.tensor([0, 0, 1, 1])
        candidates[4, 10] = 1
        self.assertEqual(
            program_properties(candidates).tolist(),
            [[1, 1], [-1, 1], [-1, 1], [1, 1], [0, 0]],
        )

    def test_unknown_base_flag_does_not_make_a_real_numeric_candidate_null(self):
        candidates = torch.zeros(1, 12)
        candidates[0, 0] = candidates[0, 4] = candidates[0, 10] = 1
        self.assertEqual(program_properties(candidates).tolist(), [[1, 1]])

    def test_candidate_only_invariant_to_field_and_context_changes(self):
        model = make("candidate_only", 396, 1175, 12, 19)
        with torch.no_grad():
            for parameter in model.parameters():
                parameter.add_(torch.randn_like(parameter) * 0.02)
        candidates = torch.randn(5, 12)
        first = model(torch.randn(396), torch.randn(1175), candidates)
        second = model(torch.randn(396), torch.randn(1175), candidates)
        self.assertTrue(torch.equal(first, second))

    def test_no_crop_geometry_mask_preserves_text_and_proposed_role_information(self):
        model = make("without_crop_encoder_and_geometry", 396, 1175, 12, 19)
        with torch.no_grad():
            for parameter in model.parameters():
                parameter.add_(torch.randn_like(parameter) * 0.02)
        field, context, numeric = (
            torch.randn(396),
            torch.randn(1175),
            torch.randn(5, 12),
        )
        other = context.clone()
        other[384:1152] = torch.randn(768)
        other[-8:] = torch.randn(8)
        self.assertTrue(
            torch.equal(model(field, context, numeric), model(field, other, numeric))
        )

    def test_all_ablation_batches_and_zero_initialized_priors_valid(self):
        for method in NEW_METHODS:
            model = make(method, 396, 1175, 12, 19)
            fields, contexts, numeric = (
                torch.randn(3, 396),
                torch.randn(3, 1175),
                torch.randn(3, 4, 12),
            )
            separate = torch.cat(
                [model(f, c, n) for f, c, n in zip(fields, contexts, numeric)]
            )
            self.assertTrue(torch.equal(separate, torch.zeros(12)))
            with torch.no_grad():
                for p in model.parameters():
                    p.add_(torch.randn_like(p) * 0.01)
            separate = torch.cat(
                [model(f, c, n) for f, c, n in zip(fields, contexts, numeric)]
            )
            batched = model(
                fields.repeat_interleave(4, 0),
                contexts.repeat_interleave(4, 0),
                numeric.flatten(0, 1),
            )
            self.assertTrue(torch.allclose(separate, batched, atol=1e-6))


if __name__ == "__main__":
    unittest.main()
