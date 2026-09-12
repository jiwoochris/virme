import os
import unittest
from contextlib import contextmanager

import torch

from device import (
    device_report,
    resolve_device,
    resolve_precision,
    serializes_requests,
)


@contextmanager
def environment(**values: str | None):
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class DeviceResolutionTests(unittest.TestCase):
    def test_pins_the_device_when_the_environment_asks_for_one(self):
        with environment(VIRME_DEVICE="cpu"):
            self.assertEqual(resolve_device().type, "cpu")

    def test_rejects_a_device_name_it_does_not_know(self):
        with environment(VIRME_DEVICE="tpu"):
            with self.assertRaises(ValueError):
                resolve_device()

    def test_refuses_a_device_this_machine_does_not_have(self):
        missing = "cuda" if not torch.cuda.is_available() else "mps"
        if missing == "mps" and torch.backends.mps.is_available():
            self.skipTest("this machine has both backends")
        with environment(VIRME_DEVICE=missing):
            with self.assertRaises(RuntimeError):
                resolve_device()

    def test_falls_back_to_an_available_backend_when_left_on_auto(self):
        for value in (None, "", "auto"):
            with environment(VIRME_DEVICE=value):
                device = resolve_device()
                self.assertIn(device.type, ("cuda", "mps", "cpu"))
                if torch.cuda.is_available():
                    self.assertEqual(device.type, "cuda")


class PrecisionTests(unittest.TestCase):
    def test_keeps_fp32_off_cuda(self):
        with environment(VIRME_PRECISION="auto"):
            self.assertEqual(resolve_precision(torch.device("cpu")), torch.float32)
            self.assertEqual(resolve_precision(torch.device("mps")), torch.float32)

    def test_picks_fp16_on_cuda_by_default(self):
        with environment(VIRME_PRECISION="auto"):
            self.assertEqual(resolve_precision(torch.device("cuda")), torch.float16)

    def test_refuses_half_precision_without_cuda(self):
        with environment(VIRME_PRECISION="fp16"):
            with self.assertRaises(RuntimeError):
                resolve_precision(torch.device("mps"))

    def test_honours_a_request_to_stay_on_fp32(self):
        with environment(VIRME_PRECISION="fp32"):
            self.assertEqual(resolve_precision(torch.device("cuda")), torch.float32)

    def test_rejects_a_precision_it_does_not_know(self):
        with environment(VIRME_PRECISION="bf16"):
            with self.assertRaises(ValueError):
                resolve_precision(torch.device("cuda"))


class ConcurrencyTests(unittest.TestCase):
    def test_serializes_everything_except_cuda(self):
        self.assertTrue(serializes_requests(torch.device("mps")))
        self.assertTrue(serializes_requests(torch.device("cpu")))
        self.assertFalse(serializes_requests(torch.device("cuda")))

    def test_reports_the_device_the_service_ended_up_on(self):
        report = device_report(torch.device("cpu"), torch.float32)
        self.assertEqual(report["device"], "cpu")
        self.assertEqual(report["precision"], "fp32")
        self.assertFalse(report["concurrent_requests"])


if __name__ == "__main__":
    unittest.main()
