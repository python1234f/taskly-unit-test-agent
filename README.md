# Taskly

Taskly is a React and FastAPI task manager originating from [cf-recruitment/taskly-app](https://github.com/cf-recruitment/taskly-app). This extension adds a constrained AI unit test agent, reusable skills, and a GitHub PR workflow. The existing application and project history are preserved.

The agent uses OpenAI to analyze changes, request related files, select testing guidance, and propose tests. Tests run separately without model credentials. Verified changes can be delivered as a draft PR targeting the original author's branch; an artifact remains available if PR creation fails.

**Validation status:** local tests and replay scenarios have run. A live model call and a hosted GitHub end-to-end run have not been performed. See [SOLUTION.md](SOLUTION.md) for architecture, limitations, and the remaining acceptance check.

## Prerequisites

- Node.js >=22.12; CI test containers use Node 22.
- Python 3.12 for the backend and agent.
- Docker Engine with Linux containers and Docker Compose.
- A public GitHub repository and an OpenAI API key for the hosted agent. Local replay needs neither a key nor GitHub credentials.

## Local setup

From the repository root:

```sh
cp .env.example .env
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r backend/requirements.txt
cd frontend
npm ci
```

The root `.env` configures the local database. Do not put model credentials in tracked files.

## Run the application

From the repository root, start the complete application:

```sh
docker compose up --build -d --wait
```

Application: http://localhost:8080. API documentation: http://localhost:8000/docs. Stop with `docker compose down`.

For separate development processes, start PostgreSQL with `docker compose up -d --wait postgres`. In `backend`, run `python -m uvicorn app.main:app --reload` with the venv activated. In `frontend`, run `npm run dev` for http://localhost:5173 or `npm run storybook` for http://localhost:6006.

## Tests and builds

In `frontend`:

```sh
npm test
npm run test:coverage
npm run build
npm run build-storybook
npm audit
```

In `backend`, with Python 3.12 and the venv activated:

```sh
python -m pytest
python -m pytest --cov=app --cov-report=term-missing --cov-report=html
```

Coverage reports are written to `frontend/coverage` and `backend/coverage/html`. Run backend coverage from `backend` so `pyproject.toml` is used.

The controller itself needs only the Python standard library. From the repository root:

```sh
python -m unittest discover -s agent/tests -v
python -m agent.tests.integration --build-images
python -m agent.runner local --scenario agent/tests/fixtures/backend.json --output .agent-output/backend
python -m agent.runner local --scenario agent/tests/fixtures/frontend.json --output .agent-output/frontend
python -m agent.runner local --scenario agent/tests/fixtures/documentation.json --output .agent-output/documentation
```

Integration tests execute real pytest and React/Vitest cases in Docker, including an import failure followed by a repair. Model responses and PR identities in these scenarios are fixtures. They are not live AI or GitHub demonstrations. Outputs include `state.json`, `report.md`, a patch, and test files. Production files are not changed by replay.

## GitHub Actions setup

Merge the controller, workflows, skills, and dependency fixes into your own repository's default branch first. Preserve the upstream history; this setup does not require rewriting it or changing the local remote automatically.

1. In **Settings → Actions → General**, enable Actions. Under **Workflow permissions**, keep the default token permissions read-only and enable **Allow GitHub Actions to create and approve pull requests**. The publisher explicitly requests write permissions; the controller does not approve or merge PRs. Organization policy may require an administrator.
2. In **Settings → Environments**, create **unit-test-agent**. Under **Deployment branches and tags**, choose **Selected branches and tags**, add a **Branch** rule for the exact default branch, usually `main`, and add no tag or PR-merge-ref rules. Do this before adding the key. Do not use an unrestricted environment or rely on “Protected branches only” without actual protection rules.
3. Add **OPENAI_API_KEY** as an environment secret and **OPENAI_MODEL** as an environment variable. Choose a model available to the account that supports Responses API strict Structured Outputs. Do not add the key as a repository-level secret. No PAT or GitHub CLI is required.
4. Protect the default branch against unreviewed workflow/controller changes. Workflows use fresh GitHub-hosted Ubuntu 24.04 runners, not shared self-hosted machines.
5. Open a small application-only PR from another branch in the same repository, or choose **Actions → Unit Test Agent → Run workflow**, select the default branch, and enter the open PR number.

Automatic triggers are PR `opened`, `synchronize`, and `reopened`. Forks and `test-agent/` branches do not receive automatic model execution. Dependency, workflow, agent-control, and test-configuration changes require human review.

Check the run's analyzed SHA, loaded skill hashes, exit codes, collected generated cases, original-PR comment, and delivery artifact. A passing generation should also create one draft PR targeting the source branch. Missing model configuration produces a controlled failure; disabled PR creation leaves the artifact and a report explaining the failure.

PRs created or updated with `GITHUB_TOKEN` can produce **approval-required** workflow runs for `opened`, `synchronize`, and `reopened`. A maintainer can select **Approve workflows to run**. The reserved branch guard prevents another agent-generated PR even after approval. See the [official token documentation](https://docs.github.com/en/actions/concepts/security/github_token).

To recover changes, download the delivery artifact, inspect its status and `identity.head_sha`, and use a clean checkout at that SHA. Review `generated-tests.patch`, then run `git apply --check generated-tests.patch` before applying it. Unverified proposals are clearly marked and must not be treated as passing tests. Nothing is merged automatically.

## Dependency security updates

| Packages | Before | After | Purpose |
|---|---|---|---|
| `storybook`, `@storybook/react-vite` | 10.6.1 | 10.6.3 | Storybook origin/code-injection fixes; matching integration version |
| `vitest`, `@vitest/coverage-v8` | 4.1.11 | 4.1.12 | Mocker injection/path fixes; matching coverage provider |
| `vite` | 6.4.3 | 6.4.5 | Development-server file access, script injection, and editor-command fixes |
| `certifi` | 2026.7.22 | 2026.10.19 | Remove affected emSign trust roots |
| `opentelemetry-api` | 1.45.0 | 1.45.1 | Bound `tracestate` parsing |

Container builds also upgrade bundled npm 10.9.9 to compatible npm 11.17.0 to address its vulnerable dependencies. Test containers remove npm, Corepack, and Yarn after installing the locked dependencies; Node stays on major 22.

Trivy additionally found an old Go standard library inside the published esbuild binary. Docker builds compile the unchanged esbuild 0.25.12 source with pinned Go 1.26.10, preserving Vite 6 compatibility. Go is only a build stage; it is not an application or agent prerequisite. Plain host `npm ci` still installs the upstream native binary, so the validated Docker path provides this additional hardening.

On 2026-10-22, `npm audit` reported **0 vulnerabilities**, OSV reported no matches for all 349 frontend lockfile entries, and `pip-audit` reported **No known vulnerabilities found** for the pinned backend requirements. Frontend tests, TypeScript/Vite build, Storybook build, and backend tests passed after the dependency changes.

These results do not mean the Docker images are vulnerability-free. Final Trivy scans of the test images reported **0 CRITICAL** findings, **43 HIGH** frontend package/CVE records and **44 HIGH** backend records. These are eight distinct Debian CVEs with no fixed version supplied by the feed, not 87 independent bugs. No fixable HIGH/CRITICAL language-package findings remained after the container hardening. See [validation notes](docs/validation.md) for scan scope, remaining findings, coverage, and exact test results.
