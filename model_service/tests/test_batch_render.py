import unittest

import torch

from latent_control import ModelRequest, derive_control
from stylegan_runtime import DEFAULT_MODEL, STYLEGAN_ROOT, StyleGanRuntime


def request_at(seed: int, state: str = "novelty") -> ModelRequest:
    return ModelRequest(
        signals=[0.18, 0.78, 0.22, 0.41, 0.68],
        activity=0.72,
        change=0.36,
        confidence=0.88,
        intensity=0.86,
        state=state,
        seed=seed,
    )


@unittest.skipUnless(
    DEFAULT_MODEL.exists() and STYLEGAN_ROOT.exists(),
    "checkpoint or runtime missing; run scripts/setup-stylegan.sh",
)
class BatchRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = StyleGanRuntime()
        cls.runtime.load()

    def structures(self, requests, states=None):
        controls = [derive_control(request) for request in requests]
        states = states or [request.state for request in requests]
        return self.runtime._render_structures(controls, states)

    def test_renders_one_frame_per_request(self):
        requests = [request_at(609348), request_at(123456), request_at(777777, "conflict")]
        structures = self.structures(requests)
        self.assertEqual(len(structures), len(requests))
        for structure in structures:
            self.assertEqual(structure.dtype, torch.uint8)
            self.assertEqual(structure.shape[0], 3)

    def test_keeps_each_request_on_its_own_seeds(self):
        requests = [request_at(609348), request_at(123456)]
        batched = self.structures(requests)
        alone = [self.structures([request])[0] for request in requests]
        for index, (many, one) in enumerate(zip(batched, alone)):
            # Batching regroups the floating point work, so the last digit can move.
            # A visible mix-up between requests would land far above this.
            difference = (many.to(torch.int16) - one.to(torch.int16)).abs().max()
            self.assertLessEqual(int(difference), 2, f"frame {index} drifted too far from its own render")

    def test_separates_frames_that_ask_for_different_seeds(self):
        first, second = self.structures([request_at(609348), request_at(123456)])
        difference = (first.to(torch.int16) - second.to(torch.int16)).abs().max()
        self.assertGreater(int(difference), 8)

    def test_groups_requests_that_share_a_truncation(self):
        shared = request_at(609348)
        repeated = self.structures([shared, shared, shared])
        for structure in repeated[1:]:
            self.assertTrue(torch.equal(repeated[0], structure))

    def test_reports_the_batch_size_it_rendered(self):
        _, metadata = self.runtime.generate_many([request_at(609348), request_at(123456)])
        self.assertEqual(metadata[0]["batch_size"], 2)
        self.assertEqual(metadata[0]["seed"], 609348)
        self.assertEqual(metadata[1]["seed"], 123456)

    def test_returns_nothing_for_an_empty_batch(self):
        payloads, metadata = self.runtime.generate_many([])
        self.assertEqual(payloads, [])
        self.assertEqual(metadata, [])


if __name__ == "__main__":
    unittest.main()
