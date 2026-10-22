import json
import unittest

from agent.contracts import AgentError
from agent.llm_client import ResponsesClient


class ModelErrorTests(unittest.TestCase):
    def test_incomplete_malformed_and_refused_outputs_fail_closed(self):
        envelopes = [
            {"status": "incomplete", "usage": {"input_tokens": 10, "output_tokens": 4}},
            {"status": "completed", "output": ["not-an-output-object"]},
            {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "Declined"}]}]},
        ]
        for envelope in envelopes:
            client = ResponsesClient("unit-test-key", "configured-model", transport=lambda request, timeout: json.dumps(envelope).encode())
            with self.subTest(status=envelope["status"]), self.assertRaises(AgentError) as caught:
                client.complete("Trusted instructions", {})
            self.assertEqual(caught.exception.code, "MODEL_ERROR")
            self.assertEqual(client.usage["output_tokens"], envelope.get("usage", {}).get("output_tokens", 0))
