import base64
import difflib
import html
import json
import re
from pathlib import Path
from urllib.parse import quote

from .contracts import AgentError, Identity, require
from .safety import artifact_path, checked_local_path, test_names
from .state import validate_state

REPORT_MARKER = "<!-- taskly-unit-test-agent:v1 -->"


def escaped(value: str) -> str:
    clean = "".join(character for character in value if character in "\n\t" or ord(character) >= 32)
    clean = html.escape(clean).replace("@", "@\u200b")
    return re.sub(r"([\\`*_{}\[\]()#+|])", r"\\\1", clean)


def render_report(state: dict, artifact_url: str, draft_url: str = "", publication_error: str = "") -> str:
    identity = Identity.from_dict(state["identity"])
    new_cases = sum(len(test_names(item["content"], item["path"].endswith(".py"))) for item in state["changes"])
    lines = [REPORT_MARKER, f"<!-- run:{identity.run_id}:{identity.run_attempt} -->", "## Unit Test Agent Report", "",
             f"PR: #{identity.pr_number}", f"Analyzed commit: `{identity.head_sha}`", f"Base commit: `{identity.base_sha}`", "",
             f"**Status:** {state['status']}", f"**Decision:** {state['decision']}", "", escaped(state["reason"]), "",
             f"Execution mode: {state['execution_mode']}. Model: {escaped(state['model'])}.", "",
             f"Files created: {sum(item['operation'] == 'create' for item in state['changes'])}. "
             f"Existing files extended: {sum(item['operation'] == 'append' for item in state['changes'])}. "
             f"Proposed new test functions / literal Vitest cases: {new_cases}.", "", "### Skills actually loaded", ""]
    if not state["skills"]:
        lines.append("No skill was loaded; execution stopped before model analysis.")
    for record in state["skills"]:
        lines.append(f"- `{record['name']}` at `{record['stage']}`, SHA-256 `{record['sha256']}`: {escaped(record['reason'])}")
    if state["behavioral_expectations"]:
        lines += ["", "### Behavioral expectations", ""]
        lines += ["- " + escaped(expectation) for expectation in state["behavioral_expectations"]]
    if state["changes"]:
        lines += ["", "### Test file changes", ""]
        lines += [f"- `{item['operation']}` `{item['path']}`; result SHA-256 `{item['result_sha256']}`." for item in state["changes"]]
    lines += ["", "### Verification", ""]
    if not state["verification"]:
        lines.append("Not run. No passing test result is claimed.")
    for record in state["verification"]:
        lines.append(f"Attempt {record['attempt']}: **{record['status']}**.")
        for component, results in record["components"].items():
            for phase in ("baseline", "targeted", "full"):
                suite = results.get(phase)
                if suite:
                    passed = sum(case["passed"] for case in suite["cases"])
                    lines.append(f"- {component} {phase}: exit {suite['exit_code']}; {passed}/{len(suite['cases'])} recorded cases passed.")
            comparison = results.get("coverage_comparison")
            if comparison:
                lines.append(f"- {component} coverage ({comparison['metric']}): {comparison['before']:.2f}% → {comparison['after']:.2f}% on the same production snapshot.")
            else:
                lines.append(f"- {component} comparable coverage: unavailable.")
    if state.get("review"):
        lines += ["", "Quality review: " + escaped(state["review"])]
    lines += ["", f"Model calls: {state['usage']['calls']}; reported input tokens: {state['usage']['input_tokens']}; "
              f"reported output tokens: {state['usage']['output_tokens']}.", "", f"[Download reports and test changes]({artifact_url})"]
    if draft_url:
        lines.append(f"[Draft PR with verified tests]({draft_url})")
    if publication_error:
        lines += ["", "Draft PR was not delivered: " + escaped(publication_error) + " The artifact remains available."]
    lines += ["", "This report applies only to the analyzed SHA. No automatic merge is performed."]
    return "\n".join(lines)


def deliver_files(state: dict, repository, output: Path) -> None:
    state_path = artifact_path(output, "state.json")
    tests_root = output / "tests"
    require(not tests_root.is_symlink(), "Artifact test root cannot be a symlink.", "UNSAFE_PATH")
    patches = []
    for item in state["changes"]:
        original = repository.read(item["path"]) if item["operation"] == "append" else ""
        content = original + ("\n\n" if original else "") + item["content"]
        for line in difflib.unified_diff(original.splitlines(keepends=True), content.splitlines(keepends=True),
                                         fromfile="a/" + item["path"] if original else "/dev/null", tofile="b/" + item["path"]):
            patches.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
        destination = checked_local_path(tests_root, item["path"], test_only=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    artifact_path(output, "generated-tests.patch").write_text("".join(patches), encoding="utf-8")
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def draft_pull(github, state: dict, repository) -> str:
    identity = Identity.from_dict(state["identity"])
    pull = github.assert_current(identity)
    require(state["status"] == "VERIFIED" and state["execution_mode"] == "live", "Only live, verified test changes may create a PR.", "PUBLISH_REFUSED")
    head = github.request("GET", f"{github.prefix}/git/commits/{identity.head_sha}")
    entries = []
    for item in state["changes"]:
        original = repository.read(item["path"]) if item["operation"] == "append" else ""
        content = original + ("\n\n" if original else "") + item["content"]
        blob = github.request("POST", f"{github.prefix}/git/blobs", {"content": base64.b64encode(content.encode()).decode(), "encoding": "base64"})
        entries.append({"path": item["path"], "mode": "100644", "type": "blob", "sha": blob["sha"]})
    tree = github.request("POST", f"{github.prefix}/git/trees", {"base_tree": head["tree"]["sha"], "tree": entries})
    branch = f"test-agent/pr-{identity.pr_number}-{identity.head_sha}"
    reference_path = f"{github.prefix}/git/ref/heads/{branch}"
    try:
        reference = github.request("GET", reference_path)
    except AgentError as error:
        if error.code != "GITHUB_HTTP_404":
            raise
        reference = None
    if reference is not None:
        existing = github.request("GET", f"{github.prefix}/git/commits/{reference['object']['sha']}")
        require(existing["tree"]["sha"] == tree["sha"] and [parent["sha"] for parent in existing["parents"]] == [identity.head_sha],
                "The generated branch already contains different changes; it was not overwritten.", "PUBLISH_CONFLICT")
    else:
        commit = github.request("POST", f"{github.prefix}/git/commits", {"message": f"test: cover PR #{identity.pr_number} at {identity.head_sha[:12]}",
                                                                 "tree": tree["sha"], "parents": [identity.head_sha]})
        github.assert_current(identity)
        github.request("POST", f"{github.prefix}/git/refs", {"ref": "refs/heads/" + branch, "sha": commit["sha"]})
    owner = identity.repository.split("/")[0]
    existing_pulls = github.pages(f"{github.prefix}/pulls?state=all&head={quote(owner + ':' + branch, safe='')}")
    if existing_pulls:
        require(len(existing_pulls) == 1 and existing_pulls[0]["base"]["ref"] == pull["head"]["ref"] and existing_pulls[0]["state"] == "open" and existing_pulls[0].get("draft"),
                "A previous generated PR exists in a different state; no duplicate was opened.", "PUBLISH_CONFLICT")
        return f"https://github.com/{identity.repository}/pull/{existing_pulls[0]['number']}"
    github.assert_current(identity)
    created = github.request("POST", f"{github.prefix}/pulls", {
        "title": f"test: generated coverage for PR #{identity.pr_number}", "head": branch, "base": pull["head"]["ref"], "draft": True,
        "body": f"Verified unit tests for #{identity.pr_number} at `{identity.head_sha}`.\n\n"
                "This PR targets the original author's branch and contains only generated test additions. "
                "Review the assertions before merging. The agent will not generate another PR from this branch.\n\n"
                f"Run: https://github.com/{identity.repository}/actions/runs/{identity.run_id}\n",
    })
    return f"https://github.com/{identity.repository}/pull/{created['number']}"


def publish(github, state: dict, repository, root: Path, artifact_url: str) -> dict:
    identity = Identity.from_dict(state["identity"])
    require(state["execution_mode"] == "live", "Replay results cannot be published to GitHub.", "PUBLISH_REFUSED")
    validate_state(state, identity, root, repository)
    github.assert_current(identity, allow_fork=state["status"] == "SKIPPED")
    draft_url = ""
    publication_error = ""
    if state["status"] == "VERIFIED":
        try:
            draft_url = draft_pull(github, state, repository)
        except AgentError as error:
            if error.code == "STALE":
                raise
            publication_error = str(error)
    body = render_report(state, artifact_url, draft_url, publication_error)
    require(len(body.encode()) <= 60_000, "Report exceeds the GitHub comment limit.")
    comments = github.pages(f"{github.prefix}/issues/{identity.pr_number}/comments")
    matches = [item for item in comments if item.get("user", {}).get("login") == "github-actions[bot]" and item.get("body", "").startswith(REPORT_MARKER)]
    require(len(matches) <= 1, "Multiple agent report comments require manual cleanup.", "PUBLISH_CONFLICT")
    if matches:
        previous = re.search(r"<!-- run:(\d+):(\d+) -->", matches[0]["body"])
        require(previous is None or tuple(map(int, previous.groups())) <= (identity.run_id, identity.run_attempt), "A newer report already exists.", "STALE")
    github.assert_current(identity, allow_fork=state["status"] == "SKIPPED")
    if matches:
        github.request("PATCH", f"{github.prefix}/issues/comments/{matches[0]['id']}", {"body": body})
    else:
        github.request("POST", f"{github.prefix}/issues/{identity.pr_number}/comments", {"body": body})
    return {"status": state["status"], "draft_url": draft_url, "publication_error": publication_error}
