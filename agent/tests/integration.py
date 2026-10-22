import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.runner import ROOT, build_images, local_run
from agent.verification import run_container


class DockerIntegrationTests(unittest.TestCase):
    def scenario(self, name: str) -> dict:
        return json.loads((ROOT / f"agent/tests/fixtures/{name}.json").read_text(encoding="utf-8"))

    def run_scenario(self, name: str, scenario: dict) -> dict:
        output = ROOT / ".agent-output/integration" / name
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenario.json"
            path.write_text(json.dumps(scenario), encoding="utf-8")
            return local_run(path, output, rebuild=False)

    def test_backend_generated_case_runs_in_real_pytest(self):
        state = self.run_scenario("backend", self.scenario("backend"))
        self.assertEqual(state["status"], "VERIFIED")
        result = state["verification"][0]["components"]["backend"]
        self.assertEqual(state["decision"], "UPDATE_TESTS")
        self.assertEqual(sum(case["name"] == "test_delete_missing_task_has_no_persistence_side_effects" for case in result["targeted"]["cases"]), 1)
        self.assertGreater(len(result["full"]["cases"]), len(result["baseline"]["cases"]))

    def test_frontend_generated_react_case_runs_in_real_vitest(self):
        state = self.run_scenario("frontend", self.scenario("frontend"))
        self.assertEqual(state["status"], "VERIFIED")
        result = state["verification"][0]["components"]["frontend"]
        self.assertEqual(len(result["targeted"]["cases"]), 1)
        self.assertGreater(len(result["full"]["cases"]), len(result["baseline"]["cases"]))

    def test_documentation_replay_does_not_execute_containers(self):
        with patch("agent.runner.verify", side_effect=AssertionError("No verification should run")):
            state = self.run_scenario("documentation", self.scenario("documentation"))
        self.assertEqual(state["status"], "NO_TESTS_NEEDED")
        self.assertEqual(state["verification"], [])

    def test_generated_import_error_is_repaired_and_reexecuted(self):
        scenario = self.scenario("backend")
        correct = copy.deepcopy(scenario["responses"][2])
        broken = copy.deepcopy(correct)
        broken["proposed_changes"][0]["content"] = broken["proposed_changes"][0]["content"].replace(
            "    session =", "    from app.features.todos.missing_repository import TodoRepository\n\n    session =", 1)
        repair = copy.deepcopy(correct)
        repair.update(assessment="TECHNICAL_TEST_ERROR", reason="Correct the generated import without changing assertions.")
        scenario["responses"] = scenario["responses"][:2] + [broken, repair, scenario["responses"][3]]
        state = self.run_scenario("repair", scenario)
        self.assertEqual(state["status"], "VERIFIED")
        self.assertEqual(state["attempt"], 1)
        self.assertEqual([record["status"] for record in state["verification"]], ["TEST_FAILURE", "PASSED"])

    def test_runtime_isolation_is_effective_with_host_credential_sentinels(self):
        content = '''import errno
import os
import socket
from pathlib import Path

import pytest


def test_runtime_boundaries():
    assert os.getuid() != 0
    assert "OPENAI_API_KEY" not in os.environ
    assert "GITHUB_TOKEN" not in os.environ
    assert "DOCKER_CONFIG" not in os.environ
    assert os.environ.get("HTTP_PROXY", "") == ""
    assert os.environ.get("HTTPS_PROXY", "") == ""
    assert os.environ.get("http_proxy", "") == ""
    assert os.environ.get("https_proxy", "") == ""
    assert not Path("/var/run/docker.sock").exists()
    assert not Path("/workspace/.env").exists()
    assert not Path("/workspace/.git").exists()
    assert os.statvfs("/").f_flag & os.ST_RDONLY
    assert os.statvfs("/workspace").f_flag & os.ST_RDONLY
    with pytest.raises(OSError) as blocked:
        Path(__file__).write_text("changed")
    assert blocked.value.errno == errno.EROFS
    status = Path("/proc/self/status").read_text()
    assert "NoNewPrivs:\\t1" in status
    assert "CapEff:\\t0000000000000000" in status
    assert Path("/sys/fs/cgroup/memory.max").read_text().strip() == "2147483648"
    assert Path("/sys/fs/cgroup/pids.max").read_text().strip() == "256"
    assert Path("/sys/fs/cgroup/cpu.max").read_text().strip() == "200000 100000"
    with socket.socket() as connection:
        connection.settimeout(0.2)
        with pytest.raises(OSError):
            connection.connect(("198.51.100.1", 443))
'''
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input"
            test_file = source / "backend/tests/test_isolation.py"
            test_file.parent.mkdir(parents=True)
            test_file.write_text(content, encoding="utf-8")
            (source / "backend/pyproject.toml").write_text((ROOT / "backend/pyproject.toml").read_text(), encoding="utf-8")
            docker_config = root / "docker-config"
            docker_config.mkdir()
            (docker_config / "config.json").write_text(json.dumps({"proxies": {"default": {
                "httpProxy": "http://private-proxy-sentinel.invalid:3128", "httpsProxy": "http://private-proxy-sentinel.invalid:3128"}}}), encoding="utf-8")
            with patch.dict(os.environ, {"OPENAI_API_KEY": "host-test-sentinel", "GITHUB_TOKEN": "host-test-sentinel", "DOCKER_CONFIG": str(docker_config)}):
                result = run_container("backend", source, root / "reports", ["backend/tests/test_isolation.py"], False)
            self.assertEqual(result["exit_code"], 0, result["diagnostics"])
            self.assertEqual(len(result["cases"]), 1)
            self.assertTrue(result["cases"][0]["passed"])


if __name__ == "__main__":
    import sys

    if "--build-images" in sys.argv:
        build_images(["backend", "frontend"])
        sys.argv.remove("--build-images")
    unittest.main(verbosity=2)
