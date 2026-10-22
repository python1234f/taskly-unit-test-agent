import re
from pathlib import Path

from .contracts import DECISIONS, MAX_JSON_BYTES, Identity, decode_json, require
from .safety import digest
from .skills import CATALOG
from .verification import generated_cases_ran, validate_proposal

STATUSES = {"PENDING", "GENERATED", "EXECUTED", "VERIFIED", "NO_TESTS_NEEDED", "NEEDS_HUMAN_REVIEW", "FAILED", "SKIPPED", "STALE"}


def validate_state(value: dict, expected: Identity, root: Path, repository=None) -> dict:
    required = {"schema_version", "identity", "status", "decision", "reason", "changes", "behavioral_expectations", "skills",
                "usage", "model", "attempt", "verification", "execution_mode", "error_code"}
    require(type(value) is dict and required <= set(value) <= required | {"affected_components", "analysis_files", "review"}, "Unexpected state fields.", "INVALID_ARTIFACT")
    require(value["schema_version"] == "1" and Identity.from_dict(value["identity"]) == expected, "Execution identity mismatch.", "INVALID_ARTIFACT")
    require(type(value["status"]) is str and value["status"] in STATUSES and type(value["decision"]) is str and value["decision"] in DECISIONS, "Invalid state status.", "INVALID_ARTIFACT")
    for field, maximum in (("reason", 4000), ("model", 160), ("error_code", 100), ("review", 4000)):
        require(type(value.get(field, "")) is str and len(value.get(field, "")) <= maximum, "Invalid state text.", "INVALID_ARTIFACT")
    require(type(value["execution_mode"]) is str and value["execution_mode"] in {"live", "replay"}, "Invalid execution mode.", "INVALID_ARTIFACT")
    require(type(value["attempt"]) is int and 0 <= value["attempt"] <= 2, "Invalid repair counter.", "INVALID_ARTIFACT")
    require(type(value["behavioral_expectations"]) is list and len(value["behavioral_expectations"]) <= 12 and
            all(type(item) is str and len(item) <= 1000 for item in value["behavioral_expectations"]), "Invalid behavioral expectations.", "INVALID_ARTIFACT")
    require(type(value.get("affected_components", [])) is list and set(value.get("affected_components", [])) <= {"frontend", "backend"}, "Invalid affected components.", "INVALID_ARTIFACT")
    require(type(value.get("analysis_files", [])) is list and len(value.get("analysis_files", [])) <= 12 and
            all(type(path) is str and len(path) <= 240 and path.startswith(("backend/app/", "frontend/src/")) for path in value.get("analysis_files", [])),
            "Invalid analysis files.", "INVALID_ARTIFACT")
    require(type(value["skills"]) is list and len(value["skills"]) <= 16, "Invalid skill records.", "INVALID_ARTIFACT")
    for record in value["skills"]:
        require(type(record) is dict and set(record) == {"name", "stage", "reason", "sha256"} and type(record["name"]) is str and record["name"] in CATALOG,
                "Invalid skill record.", "INVALID_ARTIFACT")
        require(type(record["stage"]) is str and re.fullmatch(r"analysis|generation|assessment-[0-2]", record["stage"]) is not None and
                type(record["reason"]) is str and len(record["reason"]) <= 1000, "Invalid skill stage or reason.", "INVALID_ARTIFACT")
        require(record["sha256"] == digest((root / "skills" / record["name"] / "SKILL.md").read_text(encoding="utf-8")), "Skill version mismatch.", "INVALID_ARTIFACT")
    usage = value["usage"]
    require(type(usage) is dict and set(usage) == {"calls", "input_bytes", "input_tokens", "output_tokens"} and
            all(type(counter) is int and 0 <= counter <= 1_000_000 for counter in usage.values()) and usage["calls"] <= 10 and usage["input_bytes"] <= 240_000,
            "Invalid model usage.", "INVALID_ARTIFACT")
    require(type(value["changes"]) is list and len(value["changes"]) <= 6, "Invalid test changes.", "INVALID_ARTIFACT")
    if value["changes"]:
        require(repository is not None, "Test changes require source validation.", "INVALID_ARTIFACT")
        validate_proposal(value, repository)
    require(type(value["verification"]) is list and len(value["verification"]) <= 3, "Invalid verification history.", "INVALID_ARTIFACT")
    for index, record in enumerate(value["verification"]):
        require(type(record) is dict and set(record) == {"attempt", "status", "components", "diagnostics"} and record["attempt"] == index,
                "Invalid verification attempt.", "INVALID_ARTIFACT")
        require(type(record["status"]) is str and record["status"] in {"PASSED", "TEST_FAILURE", "MISSING_TESTS", "BASELINE_FAILED", "INFRASTRUCTURE_ERROR"} and
                type(record["diagnostics"]) is str and len(record["diagnostics"]) <= 16_000,
                "Invalid verification result.", "INVALID_ARTIFACT")
        require(type(record["components"]) is dict and set(record["components"]) <= {"frontend", "backend"}, "Invalid verification components.", "INVALID_ARTIFACT")
        for component, results in record["components"].items():
            require(type(results) is dict and set(results) <= {"baseline", "targeted", "full", "coverage_comparison"}, "Invalid suite results.", "INVALID_ARTIFACT")
            for phase in ("baseline", "targeted", "full"):
                suite = results.get(phase)
                if suite is None:
                    continue
                require(type(suite) is dict and set(suite) == {"exit_code", "diagnostics", "cases", "coverage"} and type(suite["exit_code"]) is int and
                        type(suite["diagnostics"]) is str and len(suite["diagnostics"]) <= 16_000 and type(suite["cases"]) is list and len(suite["cases"]) <= 2000,
                        "Invalid suite execution.", "INVALID_ARTIFACT")
                for case in suite["cases"]:
                    require(type(case) is dict and set(case) == {"name", "classname", "file", "passed"} and type(case["passed"]) is bool and
                            all(type(case[key]) is str and len(case[key]) <= 300 for key in ("name", "classname", "file")), "Invalid test case record.", "INVALID_ARTIFACT")
                if suite["coverage"] is not None:
                    validate_coverage(suite["coverage"])
            comparison = results.get("coverage_comparison")
            if comparison is not None:
                before = results.get("baseline", {}).get("coverage")
                after = results.get("full", {}).get("coverage")
                require(before is not None and after is not None and before["metric"] == after["metric"] and before["denominator"] == after["denominator"] and
                        type(comparison) is dict and set(comparison) == {"metric", "before", "after", "configuration_sha256"} and
                        comparison["metric"] == before["metric"] and comparison["before"] == before["percent"] and comparison["after"] == after["percent"] and
                        re.fullmatch(r"[0-9a-f]{64}", comparison["configuration_sha256"]) is not None,
                        "Coverage comparison is not supported by its measurements.", "INVALID_ARTIFACT")
    if value["status"] == "VERIFIED":
        require(bool(value["changes"]) and bool(value["verification"]) and value["verification"][-1]["status"] == "PASSED" and
                value["verification"][-1]["attempt"] == value["attempt"] and bool(value.get("review")) and
                any(record["name"] == "verify-tests" for record in value["skills"]), "Verified state has no supporting evidence.", "INVALID_ARTIFACT")
        components = value["verification"][-1]["components"]
        require(set(components) == set(value.get("affected_components", [])) | {item["path"].split("/")[0] for item in value["changes"]}, "Verified components are incomplete.", "INVALID_ARTIFACT")
        for component, results in components.items():
            for phase in ("baseline", "full"):
                suite = results.get(phase)
                require(suite is not None and suite["exit_code"] == 0 and bool(suite["cases"]) and all(case["passed"] for case in suite["cases"]), "Failed or empty suite cannot be verified.", "INVALID_ARTIFACT")
            if any(item["path"].startswith(component + "/") for item in value["changes"]):
                for phase in ("targeted", "full"):
                    suite = results.get(phase)
                    require(suite is not None and suite["exit_code"] == 0 and all(case["passed"] for case in suite["cases"]) and
                            generated_cases_ran(value["changes"], component, suite["cases"]), "Generated tests were not verified.", "INVALID_ARTIFACT")
    if value["status"] in {"NO_TESTS_NEEDED", "SKIPPED"}:
        require(not value["changes"], "Skipped execution cannot contain test changes.", "INVALID_ARTIFACT")
    return value


def validate_coverage(value: dict) -> None:
    require(type(value) is dict and set(value) == {"metric", "percent", "denominator"} and value["metric"] in {"lines", "lines and branches"} and
            type(value["percent"]) in {int, float} and 0 <= value["percent"] <= 100 and type(value["denominator"]) is int and value["denominator"] > 0,
            "Invalid coverage measurement.", "INVALID_ARTIFACT")
