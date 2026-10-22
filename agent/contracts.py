import json
import re
from dataclasses import asdict, dataclass
from typing import Any

MAX_JSON_BYTES = 1_000_000
MAX_FILE_BYTES = 48_000
MAX_CHANGES = 6
SKILL_NAMES = ("analyze-pr", "frontend-testing", "backend-testing", "verify-tests")
DECISIONS = ("GENERATE_TESTS", "UPDATE_TESTS", "NO_TESTS_NEEDED", "NEEDS_HUMAN_REVIEW", "FAILED")


class AgentError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def require(condition: bool, message: str, code: str = "INVALID_DATA") -> None:
    if not condition:
        raise AgentError(code, message)


def decode_json(raw: bytes | str) -> Any:
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    require(len(encoded) <= MAX_JSON_BYTES, "JSON exceeds the size limit.")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON keys are not allowed.")
            result[key] = value
        return result

    try:
        return json.loads(encoded, object_pairs_hook=unique_object,
                          parse_constant=lambda value: require(False, "Non-finite JSON numbers are not allowed."))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise AgentError("INVALID_DATA", "Invalid JSON document.") from error


def object_schema(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def text_schema(maximum: int = 4000) -> dict:
    return {"type": "string", "maxLength": maximum}


MODEL_SCHEMA = object_schema({
    "schema_version": {"type": "string", "enum": ["1"]},
    "decision": {"type": "string", "enum": list(DECISIONS)},
    "reason": text_schema(),
    "assessment": {"type": "string", "enum": ["NOT_APPLICABLE", "ACCEPT", "TECHNICAL_TEST_ERROR", "ORACLE_CONFLICT", "INFRASTRUCTURE_ERROR"]},
    "required_skills": {"type": "array", "maxItems": 4, "items": object_schema({
        "name": {"type": "string", "enum": list(SKILL_NAMES)},
        "reason": text_schema(1000),
    })},
    "requested_files": {"type": "array", "maxItems": 8, "items": text_schema(240)},
    "behavioral_expectations": {"type": "array", "maxItems": 12, "items": text_schema(1000)},
    "proposed_changes": {"type": "array", "maxItems": MAX_CHANGES, "items": object_schema({
        "path": text_schema(240),
        "operation": {"type": "string", "enum": ["create", "append"]},
        "content": text_schema(MAX_FILE_BYTES),
    })},
})


def validate_schema(value: Any, schema: dict, location: str = "response") -> None:
    kind = schema["type"]
    expected_types = {"object": dict, "array": list, "string": str, "integer": int, "boolean": bool}
    require(type(value) is expected_types[kind], f"Invalid type at {location}.")
    if "enum" in schema:
        require(value in schema["enum"], f"Invalid enum value at {location}.")
    if kind == "object":
        require(set(value) == set(schema["properties"]), f"Unexpected or missing fields at {location}.")
        for key, child in value.items():
            validate_schema(child, schema["properties"][key], f"{location}.{key}")
    elif kind == "array":
        require(len(value) <= schema["maxItems"], f"Too many items at {location}.")
        for index, child in enumerate(value):
            validate_schema(child, schema["items"], f"{location}[{index}]")
    elif kind == "string":
        require(len(value.encode("utf-8")) <= schema.get("maxLength", MAX_JSON_BYTES), f"Text too large at {location}.")


def validate_response(value: Any) -> dict:
    validate_schema(value, MODEL_SCHEMA)
    require(bool(value["reason"].strip()), "A decision needs a reason.")
    names = [item["name"] for item in value["required_skills"]]
    require(len(names) == len(set(names)), "Duplicate skill requests are not allowed.")
    paths = [item["path"] for item in value["proposed_changes"]]
    require(len(paths) == len(set(paths)), "Duplicate test paths are not allowed.")
    if value["decision"] in {"NO_TESTS_NEEDED", "NEEDS_HUMAN_REVIEW", "FAILED"}:
        require(not value["proposed_changes"], "This decision cannot contain test changes.")
    return value


@dataclass(frozen=True)
class Identity:
    repository: str
    pr_number: int
    head_sha: str
    base_sha: str
    source_run_id: int
    run_id: int
    run_attempt: int = 1

    def __post_init__(self) -> None:
        require(isinstance(self.repository, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.repository) is not None,
                "Invalid repository name.")
        for value in (self.pr_number, self.source_run_id, self.run_id, self.run_attempt):
            require(type(value) is int and value > 0, "Invalid PR or run identifier.")
        for value in (self.head_sha, self.base_sha):
            require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value) is not None, "Invalid commit SHA.")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> "Identity":
        require(type(value) is dict and set(value) == set(cls.__dataclass_fields__), "Invalid execution identity.")
        return cls(**value)
