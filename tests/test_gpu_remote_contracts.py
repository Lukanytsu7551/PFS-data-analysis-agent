import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from api import create_app
from infrastructure import gpu_connections, gpu_detect, training_device
from remote_runner import pfs_remote_runner


class GpuRemoteContractTests(unittest.TestCase):
    @staticmethod
    def _app():
        app = create_app()
        app.config.update(TESTING=True)
        return app

    def test_training_device_is_cpu_when_gpu_switch_is_off(self):
        with patch.object(gpu_detect, "get_gpu_enabled", return_value=False):
            selected = training_device.select_training_device()

        self.assertEqual("cpu", selected["device"])
        self.assertIn("开关已关闭", selected["reason"])

    def test_training_device_falls_back_to_cpu_when_cuda_is_unavailable(self):
        with (
            patch.object(gpu_detect, "get_gpu_enabled", return_value=True),
            patch.object(
                gpu_detect,
                "detect_cuda",
                return_value={"available": False, "message": "CUDA 不可用"},
            ),
        ):
            selected = training_device.select_training_device()

        self.assertEqual("cpu", selected["device"])
        self.assertEqual("CUDA 不可用", selected["reason"])

    def test_gpu_enabled_api_requires_a_boolean(self):
        app = self._app()
        with patch("api.gpu.gpu_detect.set_gpu_enabled") as setter:
            with app.test_client() as client:
                invalid = client.post("/api/gpu/enabled", json={"enabled": "true"})
                enabled = client.post("/api/gpu/enabled", json={"enabled": True})

        self.assertEqual(400, invalid.status_code)
        self.assertEqual(200, enabled.status_code)
        self.assertEqual(True, enabled.get_json()["enabled"])
        setter.assert_called_once_with(True)

    def test_connection_validation_keeps_password_out_of_local_definition(self):
        with tempfile.TemporaryDirectory(prefix="pfs-gpu-contract-") as raw:
            connection_file = Path(raw) / "gpu_connections.json"
            with patch.object(gpu_connections, "CONNECTIONS_FILE", connection_file):
                with self.assertRaisesRegex(ValueError, "密码认证"):
                    gpu_connections.create_connection(
                        {
                            "name": "未保存密码",
                            "connection_type": "ssh",
                            "auth_method": "password",
                            "host": "gpu.example.com",
                            "port": 22,
                            "username": "runner",
                            "target_port": 8000,
                        }
                    )

                created = gpu_connections.create_connection(
                    {
                        "name": "本地兼容端点",
                        "connection_type": "direct",
                        "base_url": "http://127.0.0.1:8000",
                    }
                )
                stored = connection_file.read_text(encoding="utf-8")

        self.assertEqual("direct", created["connection_type"])
        self.assertNotIn("password", stored)
        self.assertNotIn("secret", stored)

    def test_remote_runner_rejects_arbitrary_command_arguments(self):
        with patch.object(sys, "argv", ["pfs_remote_runner", "--shell", "whoami"]):
            with self.assertRaises(SystemExit) as raised:
                pfs_remote_runner.main()

        self.assertEqual(2, raised.exception.code)


if __name__ == "__main__":
    unittest.main()
