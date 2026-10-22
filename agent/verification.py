import os
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path

from .contracts import MAX_FILE_BYTES, AgentError, decode_json, require
from .safety import checked_local_path, digest, test_names, validate_changes

MAX_LOG_BYTES = 128_000
TEST_TIMEOUT = 180
CONFIGURATION = ("backend/pyproject.toml", "frontend/vite.config.ts", "frontend/tsconfig.json", "frontend/package.json")
PROXY_VARIABLES = ("HTTP_PROXY", "HTTPS_PROXY", "FTP_PROXY", "ALL_PROXY", "NO_PROXY")


def remove_container(name: str) -> None:
    try:
        subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def plain_changes(changes: list[dict]) -> list[dict]:
    return [{key: item[key] for key in ("path", "operation", "content")} for item in changes]


def validate_proposal(state: dict, repository) -> list[dict]:
    require(type(state.get("changes")) is list, "Invalid proposal list.", "INVALID_ARTIFACT")
    for item in state["changes"]:
        require(type(item) is dict and set(item) == {"path", "operation", "content", "original_sha256", "result_sha256"} and
                all(type(value) is str for value in item.values()), "Invalid proposal fields.", "INVALID_ARTIFACT")
    originals = {item["path"]: repository.read(item["path"]) for item in state["changes"] if item["path"] in repository.entries}
    checked = validate_changes(plain_changes(state["changes"]), originals)
    require(checked == state["changes"], "Test change hashes do not match the source snapshot.", "INVALID_ARTIFACT")
    return checked


def snapshot(repository, control_root: Path, target: Path) -> str:
    total = 0
    for path, entry in repository.entries.items():
        if not path.startswith(("backend/app/", "backend/tests/", "frontend/src/")):
            continue
        if not path.endswith((".py", ".ts", ".tsx", ".css", ".svg", ".json")):
            continue
        require(entry["mode"] in {"100644", "100755"} and entry.get("type", "blob") == "blob", "Symlinks and submodules are not executable inputs.", "UNSAFE_PATH")
        content = repository.read(path)
        total += len(content.encode("utf-8"))
        require(total <= 2_000_000, "Source snapshot exceeds its size limit.", "CONTEXT_LIMIT")
        destination = checked_local_path(target, path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    config_hashes = []
    for path in CONFIGURATION:
        source = control_root / path
        content = source.read_text(encoding="utf-8")
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
        config_hashes.append(digest(content))
    return digest("".join(config_hashes))


def apply_changes(target: Path, changes: list[dict]) -> None:
    for change in changes:
        path = checked_local_path(target, change["path"], test_only=True)
        original = path.read_text(encoding="utf-8") if path.exists() else ""
        require(digest(original) == change["original_sha256"], "Original test file changed before verification.", "INVALID_ARTIFACT")
        content = original + ("\n\n" if original else "") + change["content"]
        require(digest(content) == change["result_sha256"], "Generated test content hash mismatch.", "INVALID_ARTIFACT")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def container_command(component: str, source: Path, reports: Path, selected: list[str], coverage: bool, name: str) -> list[str]:
    require(component in {"backend", "frontend"}, "Unknown test component.")
    command = ["docker", "run", "--rm", "--name", name, "--network", "none", "--user", f"{os.getuid()}:{os.getgid()}",
               "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--memory", "2g", "--cpus", "2",
               "--pids-limit", "256", "--log-driver", "none", "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=128m,mode=1777",
               "--mount", f"type=bind,src={source.resolve()},dst=/workspace,readonly",
               "--mount", f"type=bind,src={reports.resolve()},dst=/reports", "--workdir", "/workspace",
               "--env", "CI=true", "--env", "TZ=UTC", "--env", "PYTHONDONTWRITEBYTECODE=1", "--env", "COVERAGE_FILE=/reports/.coverage"]
    for variable in PROXY_VARIABLES:
        command += ["--env", variable + "=", "--env", variable.lower() + "="]
    if component == "frontend":
        command += ["--tmpfs", "/workspace/frontend/node_modules:rw,exec,nosuid,nodev,size=768m,mode=1777"]
    command += [f"taskly-agent-{component}:local", "sh", "-c"]
    if component == "backend":
        command += ['cd /workspace/backend && exec "$@"', "taskly-tests",
                    "/usr/local/bin/python", "-m", "pytest", "-p", "no:cacheprovider", "-c", "pyproject.toml", "--junitxml=/reports/junit.xml"]
        if coverage:
            command += ["--cov=app", "--cov-report=json:/reports/coverage.json"]
    else:
        command += ['cp -R /opt/frontend/node_modules/. /workspace/frontend/node_modules/ && cd /workspace/frontend && /usr/local/bin/node node_modules/typescript/bin/tsc --noEmit && exec "$@"',
                    "taskly-tests", "/usr/local/bin/node", "/workspace/frontend/node_modules/vitest/vitest.mjs", "run",
                    "--maxWorkers=2", "--reporter=junit", "--outputFile=/reports/junit.xml"]
        if coverage:
            command += ["--coverage", "--coverage.reportsDirectory=/reports/coverage"]
    command += [path.removeprefix(component + "/") for path in selected]
    return command


def run_container(component: str, source: Path, reports: Path, selected: list[str], coverage: bool) -> dict:
    reports.mkdir(parents=True, exist_ok=True)
    if component == "frontend":
        (source / "frontend/node_modules").mkdir(parents=True, exist_ok=True)
    name = "taskly-unit-tests-" + uuid.uuid4().hex
    command = container_command(component, source, reports, selected, coverage, name)
    timed_out = False
    log_limited = False
    try:
        with tempfile.TemporaryFile() as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + TEST_TIMEOUT
            while process.poll() is None:
                timed_out = time.monotonic() > deadline
                log_limited = os.fstat(log.fileno()).st_size > MAX_LOG_BYTES
                if timed_out or log_limited:
                    remove_container(name)
                    process.kill()
                    break
                time.sleep(0.05)
            exit_code = process.wait(timeout=15)
            log.seek(0)
            diagnostics = log.read(12_000).decode("utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AgentError("INFRASTRUCTURE_ERROR", "The isolated Docker test process could not be completed.") from error
    finally:
        remove_container(name)
    require(not timed_out and not log_limited, "Isolated test time or diagnostic limit exceeded.", "INFRASTRUCTURE_ERROR")
    require(exit_code not in {125, 126, 127, 137}, "Container runtime failed or exceeded its resources.", "INFRASTRUCTURE_ERROR")
    return {"exit_code": exit_code, "diagnostics": diagnostics, "cases": parse_junit(reports / "junit.xml"),
            "coverage": read_coverage(component, reports) if coverage else None}


def parse_junit(path: Path) -> list[dict]:
    if not path.exists():
        return []
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_LOG_BYTES, "Invalid JUnit report.", "INVALID_ARTIFACT")
    raw = path.read_bytes()
    require(b"<!DOCTYPE" not in raw.upper() and b"<!ENTITY" not in raw.upper(), "XML declarations are not allowed in reports.", "INVALID_ARTIFACT")
    try:
        tree = ET.fromstring(raw)
    except ET.ParseError as error:
        raise AgentError("INVALID_ARTIFACT", "Malformed JUnit report.") from error
    cases = [{"name": node.attrib.get("name", "")[:300], "classname": node.attrib.get("classname", "")[:300],
              "file": node.attrib.get("file", "")[:300], "passed": not any(node.find(tag) is not None for tag in ("failure", "error", "skipped"))}
             for node in tree.iter("testcase")]
    require(len(cases) <= 2000, "Too many test cases in report.", "INVALID_ARTIFACT")
    return cases


def read_coverage(component: str, reports: Path) -> dict | None:
    path = reports / ("coverage.json" if component == "backend" else "coverage/coverage-summary.json")
    if component == "frontend" and (reports / "coverage").is_symlink():
        return None
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 1_000_000:
        return None
    try:
        data = decode_json(path.read_bytes())
        if component == "backend":
            total = data["totals"]
            return {"metric": "lines and branches", "percent": total["percent_covered"], "denominator": total["num_statements"] + total.get("num_branches", 0)}
        total = data["total"]["lines"]
        return {"metric": "lines", "percent": total["pct"], "denominator": total["total"]}
    except (AgentError, KeyError, TypeError):
        return None


def generated_cases_ran(changes: list[dict], component: str, cases: list[dict]) -> bool:
    for change in changes:
        if not change["path"].startswith(component + "/"):
            continue
        relative = change["path"].removeprefix(component + "/")
        module = relative.removesuffix(".py").replace("/", ".")
        candidates = [case for case in cases if relative in case["classname"] or module == case["classname"] or case["file"].endswith(relative)]
        for name in test_names(change["content"], component == "backend"):
            if not any(case["passed"] and (case["name"] == name or case["name"].startswith(name + "[") or case["name"].endswith(" > " + name)) for case in candidates):
                return False
    return True


def verify(state: dict, repository, control_root: Path, output: Path, executor=run_container) -> dict:
    if state["status"] != "GENERATED":
        return state
    report = {"attempt": state["attempt"], "status": "PASSED", "components": {}, "diagnostics": ""}
    try:
        checked = validate_proposal(state, repository)
        with tempfile.TemporaryDirectory(prefix="taskly-snapshot-") as directory:
            source = Path(directory)
            configuration_hash = snapshot(repository, control_root, source)
            components = sorted(set(state.get("affected_components", [])) | {item["path"].split("/")[0] for item in checked})
            baselines = {}
            for component in components:
                result = executor(component, source, output / f"attempt-{state['attempt']}-{component}-baseline", [], True)
                baselines[component] = result
                if result["exit_code"] or not result["cases"] or not all(case["passed"] for case in result["cases"]):
                    report.update(status="BASELINE_FAILED", diagnostics=result["diagnostics"])
                    report["components"][component] = {"baseline": result}
                    break
            if report["status"] == "PASSED":
                apply_changes(source, checked)
                for component in components:
                    selected = [item["path"] for item in checked if item["path"].startswith(component + "/")]
                    targeted = executor(component, source, output / f"attempt-{state['attempt']}-{component}-targeted", selected, False) if selected else None
                    full = executor(component, source, output / f"attempt-{state['attempt']}-{component}-full", [], True) if targeted is None or targeted["exit_code"] == 0 else None
                    component_report = {"baseline": baselines[component], "targeted": targeted, "full": full, "coverage_comparison": None}
                    report["components"][component] = component_report
                    if targeted and targeted["exit_code"] != 0 or full and full["exit_code"] != 0:
                        report.update(status="TEST_FAILURE", diagnostics=(targeted if targeted and targeted["exit_code"] else full)["diagnostics"])
                        break
                    if not full or not full["cases"] or not all(case["passed"] for case in full["cases"]) or (
                            targeted is not None and not all(case["passed"] for case in targeted["cases"])) or (
                            selected and (not generated_cases_ran(checked, component, targeted["cases"]) or not generated_cases_ran(checked, component, full["cases"]))):
                        report.update(status="MISSING_TESTS", diagnostics="Generated cases were absent, skipped, or did not pass.")
                        break
                    before, after = baselines[component]["coverage"], full["coverage"]
                    if before and after and before["metric"] == after["metric"] and before["denominator"] == after["denominator"]:
                        component_report["coverage_comparison"] = {"metric": before["metric"], "before": before["percent"], "after": after["percent"], "configuration_sha256": configuration_hash}
    except AgentError as error:
        report.update(status=error.code if error.code in {"INFRASTRUCTURE_ERROR", "BASELINE_FAILED"} else "INFRASTRUCTURE_ERROR", diagnostics=str(error))
    state["verification"].append(report)
    state["status"] = "EXECUTED"
    return state
