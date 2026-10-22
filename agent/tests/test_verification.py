import tempfile
import unittest
from pathlib import Path

from agent.contracts import AgentError
from agent.verification import container_command, generated_cases_ran, parse_junit


class VerificationTests(unittest.TestCase):
    def test_container_has_no_network_secrets_host_socket_or_privileged_mode(self):
        command = container_command("backend", Path("/tmp/source"), Path("/tmp/reports"), ["backend/tests/test_value.py"], True, "test-container")
        self.assertEqual(command[command.index("--network") + 1], "none")
        self.assertIn("--read-only", command)
        self.assertIn("no-new-privileges", command)
        self.assertNotIn("--privileged", command)
        self.assertNotIn("/var/run/docker.sock", " ".join(command))
        self.assertNotIn("OPENAI_API_KEY", " ".join(command))
        self.assertNotIn("GITHUB_TOKEN", " ".join(command))
        self.assertEqual(command[-1], "tests/test_value.py")

    def test_generated_cases_must_be_present_and_passed_in_the_right_file(self):
        changes = [{"path": "backend/tests/test_value.py", "content": "def test_value():\n    assert value() == 42\n"}]
        case = {"name": "test_value", "classname": "tests.test_value", "file": "", "passed": True}
        self.assertTrue(generated_cases_ran(changes, "backend", [case]))
        for cases in ([], [{**case, "passed": False}], [{**case, "classname": "tests.another_file"}]):
            self.assertFalse(generated_cases_ran(changes, "backend", cases))

    def test_junit_rejects_entities_and_counts_failures_and_skips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "junit.xml"
            path.write_text('<testsuite><testcase name="ok"/><testcase name="bad"><failure/></testcase><testcase name="skipped"><skipped/></testcase></testsuite>')
            self.assertEqual([case["passed"] for case in parse_junit(path)], [True, False, False])
            path.write_text('<!DOCTYPE testsuite [<!ENTITY value "unsafe">]><testsuite/>')
            with self.assertRaises(AgentError):
                parse_junit(path)
