import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RemoteRunnerIdentityTests(unittest.TestCase):
    def test_pfs_runner_is_the_only_remote_runner_entry(self):
        source = (ROOT / "remote_runner" / "pfs_remote_runner.py").read_text(encoding="utf-8")
        self.assertIn("--preflight", source)
        self.assertFalse((ROOT / "remote_runner" / ("b" + "aa_remote_runner.py")).exists())

    def test_pfs_runner_rejects_arbitrary_modes(self):
        result = subprocess.run(
            [sys.executable, "-m", "remote_runner.pfs_remote_runner", "--shell", "whoami"],
            cwd=ROOT, text=True, capture_output=True, check=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_ssh_command_uses_only_the_pfs_runner(self):
        source = (ROOT / "infrastructure" / "ssh_tunnel_manager.py").read_text(encoding="utf-8")
        self.assertIn("pfs_remote_runner", source)
        self.assertNotIn("remote_runner", source.replace("pfs_remote_runner", ""))
