from __future__ import annotations

import os
import sys
import types
import unittest


sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from openshelf.pipeline.gpu_preflight import (  # noqa: E402
    _collect_model_devices,
    run_gpu_preflight,
)


class _FakeCuda:
    def __init__(self, available: bool):
        self._available = available

    def is_available(self):
        return self._available

    def device_count(self):
        return 1 if self._available else 0

    def get_device_name(self, idx):
        return f"Fake CUDA {idx}"


class _FakeMps:
    def __init__(self, available: bool):
        self._available = available

    def is_available(self):
        return self._available


def _fake_torch(*, cuda: bool = False, mps: bool = False):
    return types.SimpleNamespace(
        __version__="2.9.0",
        version=types.SimpleNamespace(cuda="12.8" if cuda else None),
        cuda=_FakeCuda(cuda),
        backends=types.SimpleNamespace(mps=_FakeMps(mps)),
    )


class TestGpuPreflight(unittest.TestCase):
    def test_chatterbox_auto_uses_cuda_when_available(self):
        report = run_gpu_preflight(
            "chatterbox",
            "auto",
            torch_module=_fake_torch(cuda=True),
        )

        self.assertTrue(report.ok)
        self.assertEqual(report.resolved_device, "cuda")
        self.assertEqual(report.torch.cuda_devices, ["Fake CUDA 0"])

    def test_chatterbox_auto_fails_on_cpu_fallback(self):
        report = run_gpu_preflight(
            "chatterbox",
            "auto",
            torch_module=_fake_torch(cuda=False),
        )

        self.assertFalse(report.ok)
        self.assertEqual(report.resolved_device, "cpu")
        self.assertIn("resolved to CPU", report.messages[0].message)

    def test_chatterbox_explicit_cpu_warns_but_passes(self):
        report = run_gpu_preflight(
            "chatterbox",
            "cpu",
            torch_module=_fake_torch(cuda=False),
        )

        self.assertTrue(report.ok)
        self.assertEqual(report.resolved_device, "cpu")
        self.assertEqual(report.messages[0].level, "warning")

    def test_cuda_request_fails_when_cuda_unavailable(self):
        report = run_gpu_preflight(
            "kokoro",
            "cuda",
            torch_module=_fake_torch(cuda=False),
        )

        self.assertFalse(report.ok)
        self.assertIn("CUDA was requested", report.messages[0].message)

    def test_load_engine_reports_nonmatching_model_device(self):
        class Param:
            device = "cpu"

        class Runtime:
            def parameters(self):
                yield Param()

        class Engine:
            def _runtime(self):
                return Runtime()

        report = run_gpu_preflight(
            "chatterbox",
            "cuda",
            load_engine=True,
            torch_module=_fake_torch(cuda=True),
            engine_factory=lambda *args, **kwargs: Engine(),
        )

        self.assertFalse(report.ok)
        self.assertEqual(report.model_devices, ["cpu"])

    def test_collect_model_devices_finds_nested_devices(self):
        runtime = types.SimpleNamespace(child=types.SimpleNamespace(device="cuda:0"))

        self.assertEqual(_collect_model_devices(runtime), {"cuda:0"})


if __name__ == "__main__":
    unittest.main()
