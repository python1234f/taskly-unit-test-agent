import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .contracts import MAX_JSON_BYTES, MODEL_SCHEMA, AgentError, decode_json, require, validate_response

MAX_CALLS = 10
MAX_INPUT_BYTES = 240_000
MAX_OUTPUT_TOKENS = 4096


def send_request(request: Request, timeout: int) -> bytes:
    with urlopen(request, timeout=timeout) as response:
        raw = response.read(MAX_JSON_BYTES + 1)
    require(len(raw) <= MAX_JSON_BYTES, "Model response exceeds the size limit.", "MODEL_ERROR")
    return raw


class ResponsesClient:
    def __init__(self, api_key: str, model: str, usage: dict | None = None, transport=send_request):
        require(bool(api_key), "Configure OPENAI_API_KEY in the protected environment.", "MISSING_CONFIGURATION")
        require(bool(model) and len(model) <= 160, "Configure OPENAI_MODEL in the protected environment.", "MISSING_CONFIGURATION")
        self.api_key = api_key
        self.model = model
        self.transport = transport
        self.usage = {"calls": 0, "input_bytes": 0, "input_tokens": 0, "output_tokens": 0, **(usage or {})}

    def complete(self, instructions: str, data: dict) -> dict:
        payload = json.dumps(data, ensure_ascii=True)
        input_size = len((instructions + payload).encode("utf-8"))
        require(self.usage["calls"] < MAX_CALLS, "Model call budget exhausted.", "MODEL_LIMIT")
        require(input_size <= 80_000 and self.usage["input_bytes"] + input_size <= MAX_INPUT_BYTES,
                "Model context budget exhausted.", "MODEL_LIMIT")
        self.usage["calls"] += 1
        self.usage["input_bytes"] += input_size
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": [{"role": "user", "content": [{"type": "input_text", "text": payload}]}],
            "text": {"format": {"type": "json_schema", "name": "unit_test_agent", "strict": True, "schema": MODEL_SCHEMA}},
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "store": False,
        }
        request = Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode("utf-8"),
                          headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            response = decode_json(self.transport(request, 60))
        except HTTPError as error:
            raise AgentError("MODEL_UNAVAILABLE", f"Model API returned HTTP {error.code}; no automatic billing retry was attempted.") from error
        except (URLError, TimeoutError, socket.timeout, OSError) as error:
            raise AgentError("MODEL_UNAVAILABLE", "Model API was unavailable; no automatic billing retry was attempted.") from error
        require(type(response) is dict, "Invalid model response envelope.", "MODEL_ERROR")
        usage = response.get("usage") or {}
        require(type(usage) is dict, "Invalid model usage data.", "MODEL_ERROR")
        for name in ("input_tokens", "output_tokens"):
            value = usage.get(name, 0)
            require(type(value) is int and value >= 0, "Invalid model usage counters.", "MODEL_ERROR")
            self.usage[name] += value
        require(response.get("status") == "completed", "Model response was incomplete or failed.", "MODEL_ERROR")
        texts = []
        outputs = response.get("output", [])
        require(type(outputs) is list and len(outputs) <= 20, "Invalid model output envelope.", "MODEL_ERROR")
        for output in outputs:
            require(type(output) is dict, "Invalid model output item.", "MODEL_ERROR")
            if output.get("type") != "message":
                continue
            contents = output.get("content", [])
            require(type(contents) is list and len(contents) <= 10, "Invalid model message content.", "MODEL_ERROR")
            for content in contents:
                require(type(content) is dict, "Invalid model content item.", "MODEL_ERROR")
                require(content.get("type") != "refusal", "Model declined the request.", "MODEL_ERROR")
                if content.get("type") == "output_text":
                    require(type(content.get("text")) is str, "Invalid model output text.", "MODEL_ERROR")
                    texts.append(content["text"])
        require(len(texts) == 1, "Expected one structured model response.", "MODEL_ERROR")
        return validate_response(decode_json(texts[0]))


class ReplayClient:
    def __init__(self, responses: list[dict]):
        self.responses = iter(responses)
        self.requests = []
        self.usage = {"calls": 0, "input_bytes": 0, "input_tokens": 0, "output_tokens": 0}
        self.model = "deterministic-replay"

    def complete(self, instructions: str, data: dict) -> dict:
        require(self.usage["calls"] < MAX_CALLS, "Replay call budget exhausted.", "MODEL_LIMIT")
        self.requests.append({"instructions": instructions, "data": data})
        self.usage["calls"] += 1
        try:
            return validate_response(next(self.responses))
        except StopIteration as error:
            raise AgentError("MODEL_UNAVAILABLE", "No replay response remains.") from error
