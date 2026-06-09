import subprocess
import unittest
from unittest.mock import patch

from scripts import diagnose_architecture


class ArchitectureDiagnosticsToolAvailabilityTests(unittest.TestCase):
    def test_tool_info_reports_successful_executable_as_available(self):
        completed = subprocess.CompletedProcess(["ruff", "--version"], 0, stdout="ruff 0.15.15\n", stderr="")

        with (
            patch.object(diagnose_architecture, "_tool_executable", return_value="/venv/bin/ruff"),
            patch.object(diagnose_architecture.subprocess, "run", return_value=completed),
        ):
            result = diagnose_architecture.tool_info("ruff", "--version")

        self.assertEqual(result["command"], "ruff")
        self.assertTrue(result["available"])
        self.assertEqual(result["path"], "/venv/bin/ruff")
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["version"], "ruff 0.15.15")
        self.assertNotIn("error", result)

    def test_tool_info_reports_missing_command_without_module_as_unavailable(self):
        with patch.object(diagnose_architecture, "_tool_executable", return_value=None):
            result = diagnose_architecture.tool_info("node", "--version")

        self.assertEqual(result, {"command": "node", "available": False})

    def test_tool_info_reports_failed_python_module_fallback_as_unavailable(self):
        completed = subprocess.CompletedProcess(
            ["/venv/bin/python", "-m", "bandit", "--version"],
            1,
            stdout="",
            stderr="/venv/bin/python: No module named bandit\n",
        )

        with (
            patch.object(diagnose_architecture, "_tool_executable", return_value=None),
            patch.object(diagnose_architecture.sys, "executable", "/venv/bin/python"),
            patch.object(diagnose_architecture.subprocess, "run", return_value=completed),
        ):
            result = diagnose_architecture.tool_info("bandit", "--version", module="bandit")

        self.assertEqual(result["command"], "bandit")
        self.assertFalse(result["available"])
        self.assertEqual(result["path"], "/venv/bin/python -m bandit")
        self.assertEqual(result["returncode"], 1)
        self.assertEqual(result["error"], "/venv/bin/python: No module named bandit")
        self.assertNotIn("version", result)

    def test_tool_info_reports_subprocess_failure_as_unavailable(self):
        with (
            patch.object(diagnose_architecture, "_tool_executable", return_value="/venv/bin/radon"),
            patch.object(diagnose_architecture.subprocess, "run", side_effect=subprocess.TimeoutExpired("radon", 5)),
        ):
            result = diagnose_architecture.tool_info("radon", "--version")

        self.assertEqual(result["command"], "radon")
        self.assertFalse(result["available"])
        self.assertEqual(result["path"], "/venv/bin/radon")
        self.assertIn("timed out", result["error"])


if __name__ == "__main__":
    unittest.main()
