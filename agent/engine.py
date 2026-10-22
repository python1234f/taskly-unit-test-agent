from pathlib import Path

from .context import Context
from .contracts import AgentError, Identity, require
from .llm_client import ReplayClient
from .safety import validate_changes
from .skills import CATALOG, Skills


def new_state(identity: Identity, model) -> dict:
    return {"schema_version": "1", "identity": identity.to_dict(), "status": "PENDING", "decision": "FAILED",
            "reason": "Execution has not completed.", "changes": [], "behavioral_expectations": [], "skills": [],
            "usage": dict(model.usage), "model": model.model, "attempt": 0, "verification": [],
            "execution_mode": "replay" if isinstance(model, ReplayClient) else "live", "error_code": ""}


def fail(state: dict, error: AgentError) -> dict:
    state.update(status="NEEDS_HUMAN_REVIEW" if error.code in {"ORACLE_CONFLICT", "CONTEXT_LIMIT", "UNSAFE_PATH"} else "FAILED",
                 decision="NEEDS_HUMAN_REVIEW" if error.code in {"ORACLE_CONFLICT", "CONTEXT_LIMIT", "UNSAFE_PATH"} else "FAILED",
                 reason=str(error), error_code=error.code)
    return state


def instructions(root: Path) -> str:
    return (root / "agent/AGENT.md").read_text(encoding="utf-8")


def generate(identity: Identity, repository, changes: list[dict], model, root: Path) -> dict:
    state = new_state(identity, model)
    skills = Skills(root / "skills")
    try:
        require(0 < len(changes) <= 100, "Unsupported number of PR files.", "CONTEXT_LIMIT")
        restricted = (".github/", "agent/", "skills/")
        configuration = {"frontend/package.json", "frontend/package-lock.json", "frontend/vite.config.ts",
                         "frontend/tsconfig.json", "backend/requirements.in", "backend/requirements.txt",
                         "backend/pyproject.toml", "pytest.ini", "compose.yaml"}
        if any(item["filename"].startswith(restricted) or item["filename"] in configuration or
               file_name(item["filename"]) in {"Dockerfile", "conftest.py"} or item["filename"].startswith(".env") for item in changes):
            state.update(status="NEEDS_HUMAN_REVIEW", decision="NEEDS_HUMAN_REVIEW",
                         reason="Changes to agent controls, dependencies or test execution configuration require human review.")
            return state
        context = Context(repository, changes)
        state["analysis_files"] = [item["filename"] for item in changes if item["filename"].startswith(("backend/app/", "frontend/src/"))]
        state["affected_components"] = sorted({"backend" if item["filename"].startswith("backend/app/") else "frontend"
                                               for item in changes if item["filename"].startswith(("backend/app/", "frontend/src/"))})
        context.gather()
        base = instructions(root)
        selection = model.complete(base + "\nStage: select skills. Return no proposed changes or file requests. Select analyze-pr and any relevant testing skills.",
                                   {"untrusted_pr_changes": changes, "available_skills": CATALOG})
        require(not selection["proposed_changes"] and not selection["requested_files"], "Skill selection cannot change files.")
        selected = {item["name"]: item["reason"] for item in selection["required_skills"]}
        require("analyze-pr" in selected, "The analysis skill must be selected.")
        analysis_instructions = base + "\nStage: analyze. Do not propose test changes yet.\n" + skills.load("analyze-pr", "analysis", selected["analyze-pr"])
        analysis = None
        for unused in range(3):
            analysis = model.complete(analysis_instructions, {"untrusted_context": context.payload(), "available_skills": CATALOG})
            require(not analysis["proposed_changes"], "Analysis cannot modify files.")
            if not analysis["requested_files"]:
                break
            context.request_files(analysis["requested_files"])
        require(analysis is not None and not analysis["requested_files"], "Context retrieval did not converge.", "CONTEXT_LIMIT")
        state.update(decision=analysis["decision"], reason=analysis["reason"], behavioral_expectations=analysis["behavioral_expectations"])
        if analysis["decision"] not in {"GENERATE_TESTS", "UPDATE_TESTS"}:
            state["status"] = analysis["decision"]
            return state
        require(bool(analysis["behavioral_expectations"]), "Test generation needs explicit behavioral expectations.")
        selected.update({item["name"]: item["reason"] for item in analysis["required_skills"]})
        testing_skills = [name for name in selected if name in {"frontend-testing", "backend-testing"}]
        require(bool(testing_skills), "A testing skill must be selected before generation.")
        guidance = "\n".join(skills.load(name, "generation", selected[name]) for name in testing_skills)
        result = model.complete(base + "\nStage: generate tests. Context retrieval is complete. Preserve the supplied behavioral_expectations exactly.\n" + guidance,
                                {"untrusted_context": context.payload(), "behavioral_expectations": state["behavioral_expectations"]})
        require(not result["requested_files"], "Generation cannot request more context.", "CONTEXT_LIMIT")
        require(result["behavioral_expectations"] == state["behavioral_expectations"], "Generation changed the behavioral expectations.", "ORACLE_CONFLICT")
        require(result["decision"] in {"GENERATE_TESTS", "UPDATE_TESTS", "NEEDS_HUMAN_REVIEW"}, "Unexpected generation decision.")
        if result["decision"] == "NEEDS_HUMAN_REVIEW":
            state.update(status="NEEDS_HUMAN_REVIEW", decision="NEEDS_HUMAN_REVIEW", reason=result["reason"])
            return state
        for item in result["proposed_changes"]:
            require(item["path"] not in repository.entries or item["path"] in context.files,
                    "Existing test file must be read before it can be extended.", "CONTEXT_LIMIT")
            needed_skill = "backend-testing" if item["path"].startswith("backend/") else "frontend-testing"
            require(needed_skill in testing_skills, "The required testing skill was not loaded.")
        checked = validate_changes(result["proposed_changes"], context.originals(result["proposed_changes"]))
        state.update(status="GENERATED", changes=checked, decision="UPDATE_TESTS" if any(item["operation"] == "append" for item in checked) else "GENERATE_TESTS")
    except AgentError as error:
        fail(state, error)
    finally:
        state.update(skills=skills.records, usage=dict(model.usage))
    return state


def file_name(path: str) -> str:
    return path.rsplit("/", 1)[-1]


def assess(state: dict, repository, model, root: Path) -> dict:
    if state["status"] != "EXECUTED":
        return state
    skills = Skills(root / "skills", state["skills"])
    try:
        verification = state["verification"][-1]
        if verification["status"] in {"INFRASTRUCTURE_ERROR", "BASELINE_FAILED", "MISSING_TESTS"}:
            raise AgentError(verification["status"], "Verification failed before a trustworthy generated-test result was available.")
        guidance = skills.load("verify-tests", f"assessment-{state['attempt']}", "Evaluate actual execution and preserve the test oracle.")
        guidance += "\n" + "\n".join(skills.load(name, f"assessment-{state['attempt']}", "Apply framework guidance to diagnosis or technical repair.")
                                     for name in sorted({record["name"] for record in state["skills"]} & {"backend-testing", "frontend-testing"}))
        context = Context(repository, [{"filename": path} for path in state.get("analysis_files", [])])
        context.gather()
        for item in state["changes"]:
            if item["path"] in repository.entries:
                context.add(item["path"], required=True)
        diagnostic_summary = {"status": verification["status"], "diagnostics": verification.get("diagnostics", "")[:8000], "components": {}}
        for component, suites in verification.get("components", {}).items():
            diagnostic_summary["components"][component] = {
                phase: {"exit_code": suite["exit_code"], "collected": len(suite["cases"]),
                        "passed": sum(case["passed"] for case in suite["cases"]),
                        "failed_cases": [case for case in suite["cases"] if not case["passed"]][:8]}
                for phase in ("baseline", "targeted", "full") if (suite := suites.get(phase)) is not None}
        result = model.complete(instructions(root) + "\nStage: assess execution and optionally repair technical test errors. If passed, return ACCEPT with no changes. Never alter expectations or assertions.\n" + guidance,
                                {"untrusted_verification": diagnostic_summary, "untrusted_context": context.payload(), "untrusted_test_changes": state["changes"],
                                 "behavioral_expectations": state["behavioral_expectations"], "repairs_remaining": 2 - state["attempt"]})
        require(not result["requested_files"], "Assessment cannot request arbitrary files.")
        require(result["behavioral_expectations"] == state["behavioral_expectations"], "Assessment changed the behavioral expectations.", "ORACLE_CONFLICT")
        if verification["status"] == "PASSED":
            require(result["decision"] in {"GENERATE_TESTS", "UPDATE_TESTS"} and result["assessment"] == "ACCEPT" and not result["proposed_changes"], "Passing tests still require a positive quality review.", "ORACLE_CONFLICT")
            state.update(status="VERIFIED", review=result["reason"])
        elif result["assessment"] == "TECHNICAL_TEST_ERROR" and result["proposed_changes"]:
            require(state["attempt"] < 2, "The two-repair budget is exhausted.", "REPAIR_LIMIT")
            context = Context(repository, [])
            previous = [{key: item[key] for key in ("path", "operation", "content")} for item in state["changes"]]
            changes = validate_changes(result["proposed_changes"], context.originals(result["proposed_changes"]), previous)
            state.update(status="GENERATED", changes=changes, attempt=state["attempt"] + 1, review=result["reason"])
        else:
            raise AgentError("ORACLE_CONFLICT", "Test failure needs human review: " + result["reason"][:1000])
    except AgentError as error:
        fail(state, error)
    finally:
        state.update(skills=skills.records, usage=dict(model.usage))
    return state
