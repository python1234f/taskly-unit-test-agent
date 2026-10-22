import base64
import hashlib
import io
import json
import re
import stat
import zipfile
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

from .contracts import MAX_FILE_BYTES, MAX_JSON_BYTES, AgentError, Identity, decode_json, require
from .safety import safe_path


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, new_url):
        return None


class GitHubClient:
    def __init__(self, repository: str, token: str):
        require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository) is not None, "Invalid repository name.")
        require(bool(token), "GITHUB_TOKEN is required.", "MISSING_CONFIGURATION")
        self.repository = repository
        self.token = token
        self.prefix = f"/repos/{repository}"

    def request(self, method: str, path: str, data: dict | None = None):
        require(path == self.prefix or path.startswith(self.prefix + "/"), "GitHub request is outside the configured repository.")
        request = Request("https://api.github.com" + path, method=method,
                          data=json.dumps(data).encode() if data is not None else None,
                          headers={"Authorization": f"Bearer {self.token}", "Accept": "application/vnd.github+json",
                                   "Content-Type": "application/json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "taskly-unit-test-agent"})
        try:
            with build_opener(NoRedirect()).open(request, timeout=30) as response:
                raw = response.read(MAX_JSON_BYTES + 1)
            return decode_json(raw) if raw else None
        except HTTPError as error:
            raise AgentError(f"GITHUB_HTTP_{error.code}", f"GitHub API returned HTTP {error.code}.") from error
        except (URLError, TimeoutError, OSError) as error:
            raise AgentError("GITHUB_UNAVAILABLE", "GitHub API is unavailable.") from error

    def pages(self, path: str, limit: int = 300) -> list:
        result = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 5):
            data = self.request("GET", f"{path}{separator}per_page=100&page={page}")
            require(type(data) is list, "Invalid GitHub list response.")
            result.extend(data)
            require(len(result) <= limit, "GitHub list exceeds the supported limit.", "CONTEXT_LIMIT")
            if len(data) < 100:
                return result
        raise AgentError("CONTEXT_LIMIT", "GitHub pagination did not complete.")

    def pull(self, number: int) -> dict:
        require(type(number) is int and number > 0, "Invalid PR number.")
        pull = self.request("GET", f"{self.prefix}/pulls/{number}")
        require(pull.get("number") == number and pull.get("base", {}).get("repo", {}).get("full_name") == self.repository,
                "PR metadata does not match the repository.", "INVALID_ARTIFACT")
        return pull

    def assert_current(self, identity: Identity, allow_fork: bool = False) -> dict:
        require(identity.repository == self.repository, "Repository identity mismatch.", "INVALID_ARTIFACT")
        pull = self.pull(identity.pr_number)
        require(pull["state"] == "open" and pull["head"]["sha"] == identity.head_sha and pull["base"]["sha"] == identity.base_sha,
                "PR head or base changed; this result is stale.", "STALE")
        require(allow_fork or (pull["head"].get("repo") or {}).get("full_name") == self.repository, "Fork PRs are not eligible for automatic model execution.", "FORK_SKIPPED")
        require(not pull["head"]["ref"].startswith("test-agent/"), "Agent branches cannot start another generation run.", "AGENT_PR_SKIPPED")
        return pull

    def resolve(self, event: dict, run_id: int, run_attempt: int, manual_pr: int | None = None) -> tuple[Identity, dict]:
        if manual_pr is not None:
            pull = self.pull(manual_pr)
            source_id = run_id
        else:
            supplied = event.get("workflow_run", {})
            source_id = supplied.get("id")
            require(type(source_id) is int and source_id > 0, "Missing source workflow run.", "INVALID_ARTIFACT")
            source = self.request("GET", f"{self.prefix}/actions/runs/{source_id}")
            require(source.get("event") == "pull_request" and source.get("path") == ".github/workflows/pr-agent.yml" and source.get("conclusion") == "success",
                    "Unexpected source workflow.", "INVALID_ARTIFACT")
            pulls = source.get("pull_requests", [])
            if not pulls:
                pulls = self.pages(f"{self.prefix}/commits/{source['head_sha']}/pulls")
            pulls = [item for item in pulls if item.get("head", {}).get("sha") == source["head_sha"]]
            require(len(pulls) == 1, "Cannot identify one PR for the source run.", "INVALID_ARTIFACT")
            pull = self.pull(pulls[0]["number"])
            require(pull["head"]["sha"] == source["head_sha"], "Source workflow no longer describes the PR head.", "STALE")
        identity = Identity(self.repository, pull["number"], pull["head"]["sha"], pull["base"]["sha"], source_id, run_id, run_attempt)
        return identity, pull

    def changed_files(self, identity: Identity) -> list[dict]:
        pull = self.assert_current(identity)
        files = self.pages(f"{self.prefix}/pulls/{identity.pr_number}/files", limit=100)
        require(len(files) == pull["changed_files"], "Changed-file list is incomplete.", "CONTEXT_LIMIT")
        result = []
        total = 0
        for item in files:
            path = item["filename"]
            require(type(path) is str and len(path) <= 240, "Invalid changed-file path.", "UNSAFE_PATH")
            patch = item.get("patch", "")
            total += len(patch.encode("utf-8"))
            require(total <= 16_000, "PR diff exceeds the automatic context budget.", "CONTEXT_LIMIT")
            if path.startswith(("backend/app/", "frontend/src/")):
                safe_path(path)
                require(bool(patch), "Source diff is unavailable; human review is required.", "CONTEXT_LIMIT")
            result.append({"filename": path, "status": item["status"], "patch": patch})
        self.assert_current(identity)
        return result

    def artifact_bytes(self, artifact_id: int) -> bytes:
        require(type(artifact_id) is int and artifact_id > 0, "Invalid artifact identifier.")
        request = Request(f"https://api.github.com{self.prefix}/actions/artifacts/{artifact_id}/zip",
                          headers={"Authorization": f"Bearer {self.token}", "User-Agent": "taskly-unit-test-agent"})
        try:
            response = build_opener(NoRedirect()).open(request, timeout=30)
        except HTTPError as error:
            require(error.code == 302, "Artifact download failed.", "INVALID_ARTIFACT")
            location = error.headers.get("Location", "")
            parsed = urlsplit(location)
            require(parsed.scheme == "https" and parsed.hostname is not None and not parsed.username and not parsed.password and
                    parsed.hostname.endswith((".blob.core.windows.net", ".githubusercontent.com", ".amazonaws.com")),
                    "Unexpected artifact download destination.", "INVALID_ARTIFACT")
            response = urlopen(Request(location, headers={"User-Agent": "taskly-unit-test-agent"}), timeout=30)
        with response:
            raw = response.read(MAX_JSON_BYTES + 1)
        require(len(raw) <= MAX_JSON_BYTES, "Artifact archive is too large.", "INVALID_ARTIFACT")
        return raw

    def read_state_artifact(self, identity: Identity, stage: str) -> dict:
        require(re.fullmatch(r"[a-z]+[0-2]?", stage) is not None, "Invalid artifact stage.")
        name = f"unit-test-agent-{stage}-{identity.run_attempt}"
        metadata = self.request("GET", f"{self.prefix}/actions/runs/{identity.run_id}/artifacts?per_page=100")
        require(metadata.get("total_count", 0) <= 100, "Too many run artifacts.", "INVALID_ARTIFACT")
        matches = [item for item in metadata.get("artifacts", []) if item["name"] == name and not item.get("expired")]
        require(bool(matches), "The requested stage artifact is missing.", "ARTIFACT_MISSING")
        require(len(matches) == 1, "Expected exactly one current-stage artifact.", "INVALID_ARTIFACT")
        artifact = matches[0]
        require(artifact.get("workflow_run", {}).get("id") == identity.run_id and artifact.get("size_in_bytes", MAX_JSON_BYTES + 1) <= MAX_JSON_BYTES,
                "Artifact provenance or size mismatch.", "INVALID_ARTIFACT")
        archive = self.artifact_bytes(artifact["id"])
        expected_digest = artifact.get("digest")
        require(isinstance(expected_digest, str) and expected_digest == "sha256:" + hashlib.sha256(archive).hexdigest(),
                "Artifact archive digest mismatch.", "INVALID_ARTIFACT")
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
                entries = zipped.infolist()
                require(len(entries) == 1 and entries[0].filename == "state.json" and entries[0].file_size <= MAX_JSON_BYTES and
                        not stat.S_ISLNK(entries[0].external_attr >> 16), "Unexpected artifact archive entries.", "INVALID_ARTIFACT")
                return decode_json(zipped.read(entries[0]))
        except (zipfile.BadZipFile, OSError, RuntimeError) as error:
            raise AgentError("INVALID_ARTIFACT", "Invalid artifact archive.") from error


class GitHubRepository:
    def __init__(self, github: GitHubClient, sha: str):
        require(re.fullmatch(r"[0-9a-f]{40}", sha) is not None, "Invalid snapshot SHA.")
        self.github = github
        self.sha = sha
        self.cache = {}
        tree = github.request("GET", f"{github.prefix}/git/trees/{sha}?recursive=1")
        require(not tree.get("truncated") and len(tree.get("tree", [])) <= 2000, "Repository tree is too large or incomplete.", "CONTEXT_LIMIT")
        self.entries = {item["path"]: item for item in tree["tree"] if item["type"] != "tree"}

    def assert_controls(self, root) -> None:
        for path in ("backend/requirements.txt", "backend/pyproject.toml", "frontend/package.json", "frontend/package-lock.json",
                     "frontend/vite.config.ts", "frontend/tsconfig.json"):
            raw = (root / path).read_bytes()
            expected = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
            require(self.entries.get(path, {}).get("sha") == expected,
                    "PR dependencies or test configuration differ from the trusted controller; rebase or request human review.", "CONTEXT_LIMIT")

    def read(self, path: str) -> str:
        safe_path(path)
        require(path in self.entries, "Snapshot file is missing.", "CONTEXT_UNAVAILABLE")
        entry = self.entries[path]
        require(entry["mode"] in {"100644", "100755"} and entry["type"] == "blob", "Symlink or submodule context is not allowed.", "UNSAFE_PATH")
        require(entry.get("size", MAX_FILE_BYTES + 1) <= MAX_FILE_BYTES, "Snapshot file exceeds the size limit.", "CONTEXT_LIMIT")
        if path not in self.cache:
            blob = self.github.request("GET", f"{self.github.prefix}/git/blobs/{entry['sha']}")
            require(blob.get("encoding") == "base64", "Unsupported blob encoding.")
            try:
                raw = base64.b64decode(blob["content"].replace("\n", ""), validate=True)
                require(len(raw) <= MAX_FILE_BYTES, "Decoded blob exceeds the size limit.", "CONTEXT_LIMIT")
                require(hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == entry["sha"], "Blob hash mismatch.", "INVALID_ARTIFACT")
                self.cache[path] = raw.decode("utf-8")
            except (ValueError, UnicodeError) as error:
                raise AgentError("INVALID_DATA", "Snapshot blob is not valid UTF-8 text.") from error
        return self.cache[path]
