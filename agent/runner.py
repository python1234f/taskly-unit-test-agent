import argparse
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

from .context import LocalRepository
from .contracts import MAX_JSON_BYTES, AgentError, Identity, decode_json, require
from .engine import assess, fail, generate, new_state
from .github_client import GitHubClient, GitHubRepository
from .llm_client import ReplayClient, ResponsesClient
from .publish import deliver_files, publish, render_report
from .safety import artifact_path
from .state import validate_state
from .verification import PROXY_VARIABLES, verify

ROOT = Path(__file__).resolve().parents[1]
STAGES = ("prepared", "generated", "verify0", "review0", "verify1", "review1", "verify2", "review2")


def read_json_file(path: Path):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_JSON_BYTES, "Invalid JSON input file.")
    return decode_json(path.read_bytes())


def save_state(state: dict, output: Path) -> None:
    raw = json.dumps(state, indent=2, ensure_ascii=True) + "\n"
    require(len(raw.encode()) <= MAX_JSON_BYTES, "Execution state exceeds the artifact limit.")
    artifact_path(output, "state.json").write_text(raw, encoding="utf-8")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"status={state['status']}\n")


def build_images(components: list[str]) -> None:
    for component in components:
        require(component in {"backend", "frontend"}, "Invalid image target.")
        command = ["docker", "build", "--target", component, "--tag", f"taskly-agent-{component}:local"]
        for variable in PROXY_VARIABLES:
            command += ["--build-arg", variable + "=", "--build-arg", variable.lower() + "="]
        command += ["--file", str(ROOT / "agent/Dockerfile"), str(ROOT)]
        try:
            result = subprocess.run(command, timeout=600, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AgentError("INFRASTRUCTURE_ERROR", "The trusted test image could not be built.") from error
        require(result.returncode == 0, "The trusted test image build failed.", "INFRASTRUCTURE_ERROR")


def github_context(manual_pr: int | None = None):
    github = GitHubClient(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", ""))
    event = read_json_file(Path(os.environ["GITHUB_EVENT_PATH"]))
    metadata = github.request("GET", github.prefix)
    require(os.environ.get("GITHUB_REF") == "refs/heads/" + metadata["default_branch"], "Controller must run from the default branch.", "UNTRUSTED_CONTROLLER")
    identity, pull = github.resolve(event, int(os.environ["GITHUB_RUN_ID"]), int(os.environ.get("GITHUB_RUN_ATTEMPT", "1")), manual_pr)
    return github, identity, pull


def empty_state(identity: Identity) -> dict:
    return new_state(identity, SimpleNamespace(model="not-configured", usage={"calls": 0, "input_bytes": 0, "input_tokens": 0, "output_tokens": 0}))


def prepare(github, identity: Identity, pull: dict) -> dict:
    state = empty_state(identity)
    if (pull["head"].get("repo") or {}).get("full_name") != identity.repository:
        state.update(status="SKIPPED", decision="NEEDS_HUMAN_REVIEW", reason="Fork PRs do not receive automatic model execution or model credentials.")
    elif pull["head"]["ref"].startswith("test-agent/"):
        state.update(status="SKIPPED", decision="NO_TESTS_NEEDED", reason="Agent-generated branches are excluded to prevent recursive PR generation.")
    else:
        github.assert_current(identity)
        state["reason"] = "PR identity validated; model analysis is pending."
    return state


def load_stage(github, identity: Identity, stage: str):
    state = github.read_state_artifact(identity, stage)
    repository = GitHubRepository(github, identity.head_sha) if state.get("changes") else None
    validate_state(state, identity, ROOT, repository)
    return state, repository


def selected_state(github, identity: Identity):
    for stage in reversed(STAGES):
        try:
            state, repository = load_stage(github, identity, stage)
        except AgentError as error:
            if error.code == "ARTIFACT_MISSING":
                continue
            raise
        if state["status"] in {"PENDING", "GENERATED", "EXECUTED"}:
            fail(state, AgentError("INCOMPLETE_EXECUTION", "A required generation, verification or quality-review stage did not complete."))
        return state, repository
    raise AgentError("ARTIFACT_MISSING", "No validated controller state is available.")


def local_run(scenario: Path, output: Path, rebuild: bool) -> dict:
    fixture = read_json_file(scenario)
    require(type(fixture) is dict and set(fixture) == {"changes", "responses"}, "Invalid local replay scenario.")
    model = ReplayClient(fixture["responses"])
    repository = LocalRepository(ROOT)
    identity = Identity("local/taskly", 1, "a" * 40, "b" * 40, 1, 1)
    state = generate(identity, repository, fixture["changes"], model, ROOT)
    if rebuild and state["status"] == "GENERATED":
        build_images(sorted({item["path"].split("/")[0] for item in state["changes"]}))
    for unused in range(3):
        if state["status"] != "GENERATED":
            break
        state = verify(state, repository, ROOT, output / "verification")
        state = assess(state, repository, model, ROOT)
    validate_state(state, identity, ROOT, repository)
    save_state(state, output)
    deliver_files(state, repository, output)
    artifact_path(output, "report.md").write_text("LOCAL REPLAY: the PR identity is synthetic and model responses are fixtures.\n\n" +
                                     render_report(state, "./generated-tests.patch"), encoding="utf-8")
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description="Constrained Taskly unit test agent")
    parser.add_argument("command", choices=("prepare", "generate", "verify", "assess", "bundle", "publish", "local"))
    parser.add_argument("--input-stage", choices=STAGES)
    parser.add_argument("--pr-number", type=int)
    parser.add_argument("--output", type=Path, default=ROOT / ".agent-output")
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--build-images", action="store_true")
    arguments = parser.parse_args()
    state = None
    try:
        if arguments.command == "local":
            require(arguments.scenario is not None, "Local replay requires --scenario.")
            state = local_run(arguments.scenario, arguments.output, arguments.build_images)
        else:
            manual = arguments.pr_number
            if manual is None and os.environ.get("AGENT_PR_NUMBER"):
                supplied = os.environ["AGENT_PR_NUMBER"]
                require(supplied.isdecimal() and len(supplied) <= 10, "Manual PR number must be a positive integer.")
                manual = int(supplied)
            github, identity, pull = github_context(manual)
            if arguments.command == "prepare":
                state = prepare(github, identity, pull)
                output_file = os.environ.get("GITHUB_OUTPUT")
                if output_file:
                    with open(output_file, "a", encoding="utf-8") as stream:
                        stream.write(f"ready={'true' if state['status'] == 'PENDING' else 'false'}\npr_number={identity.pr_number}\nhead_sha={identity.head_sha}\n")
            elif arguments.command in {"bundle", "publish"}:
                state, repository = selected_state(github, identity)
                if arguments.command == "bundle":
                    deliver_files(state, repository, arguments.output)
                    artifact_path(arguments.output, "report.md").write_text(render_report(state, f"https://github.com/{identity.repository}/actions/runs/{identity.run_id}"), encoding="utf-8")
                elif pull["head"]["ref"].startswith("test-agent/"):
                    print(json.dumps({"status": "SKIPPED", "reason": "Managed PR; no recursive report or generation."}))
                    return 0
                else:
                    result = publish(github, state, repository, ROOT, f"https://github.com/{identity.repository}/actions/runs/{identity.run_id}")
                    print(json.dumps(result))
                    return 1 if state["status"] in {"FAILED", "NEEDS_HUMAN_REVIEW"} or result["publication_error"] else 0
            else:
                require(arguments.input_stage is not None, "This command requires --input-stage.")
                state, repository = load_stage(github, identity, arguments.input_stage)
                github.assert_current(identity)
                if arguments.command == "generate" and state["status"] == "PENDING":
                    repository = GitHubRepository(github, identity.head_sha)
                    repository.assert_controls(ROOT)
                    model = ResponsesClient(os.environ.get("OPENAI_API_KEY", ""), os.environ.get("OPENAI_MODEL", ""))
                    state = generate(identity, repository, github.changed_files(identity), model, ROOT)
                elif arguments.command == "verify" and state["status"] == "GENERATED":
                    repository.assert_controls(ROOT)
                    if arguments.build_images:
                        build_images(sorted(set(state.get("affected_components", [])) | {item["path"].split("/")[0] for item in state["changes"]}))
                    state = verify(state, repository, ROOT, arguments.output / "verification")
                elif arguments.command == "assess" and state["status"] == "EXECUTED":
                    model = ResponsesClient(os.environ.get("OPENAI_API_KEY", ""), os.environ.get("OPENAI_MODEL", ""), usage=state["usage"])
                    state = assess(state, repository, model, ROOT)
            save_state(state, arguments.output)
        print(json.dumps({"status": state["status"], "decision": state["decision"], "attempt": state["attempt"], "usage": state["usage"]}))
        return 1 if arguments.command == "local" and state["status"] not in {"VERIFIED", "NO_TESTS_NEEDED"} else 0
    except AgentError as error:
        if state is not None and arguments.command not in {"publish", "bundle"} and error.code != "UNSAFE_PATH":
            fail(state, error)
            save_state(state, arguments.output)
            print(json.dumps({"status": state["status"], "error_code": error.code, "reason": str(error)}))
            return 0
        print(json.dumps({"status": "STALE" if error.code == "STALE" else "FAILED", "error_code": error.code, "reason": str(error)}))
        return 1
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError, OSError):
        error = AgentError("CONTROLLER_ERROR", "Controller input, filesystem or response processing failed; no success is claimed.")
        if state is not None and arguments.command not in {"publish", "bundle"}:
            fail(state, error)
            save_state(state, arguments.output)
        print(json.dumps({"status": "FAILED", "error_code": error.code, "reason": str(error)}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
