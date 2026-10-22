# Taskly — AI Unit Test Agent

Taskly is a React/TypeScript and FastAPI task manager based on [cf-recruitment/taskly-app](https://github.com/cf-recruitment/taskly-app).

This repository adds a custom AI agent with reusable skills and an automated GitHub pull request workflow.

The agent analyzes code changes, generates or updates unit tests, runs them in isolated Docker containers, and reports results directly on GitHub. Verified tests are delivered through a separate draft PR.

## Live Demonstration

The agent has been tested with the OpenAI API and GitHub Actions.

- [Original feature PR #1](https://github.com/python1234f/taskly-unit-test-agent/pull/1)
- [Automatically generated Draft PR #2](https://github.com/python1234f/taskly-unit-test-agent/pull/2)
- [Successful GitHub Actions run](https://github.com/python1234f/taskly-unit-test-agent/actions/runs/39858909603)

**Results:** 2 generated pytest tests passed, the full backend suite passed (40/40), and coverage increased from 70.66% to 71.86%.

See [SOLUTION.md](SOLUTION.md) for architecture, agent decisions, reusable skills, and the complete demonstration.

## Features

- Custom OpenAI-powered unit test agent.
- Four reusable instruction skills.
- Backend support: Python, FastAPI, pytest.
- Frontend support: React, TypeScript, Vitest.
- Automatic PR analysis and test generation.
- Isolated test execution and result verification.
- Up to two technical repair attempts.
- GitHub PR comments, downloadable artifacts, and generated draft PRs.
- Security controls and independent validation of model output.

## Requirements

- Python 3.12
- Node.js >=22.12
- Docker Engine and Docker Compose
- Public GitHub repository for hosted execution
- OpenAI API key for the live agent

Local replay tests do not require OpenAI or GitHub credentials.

## Local Setup

From the repository root:

```bash
cp .env.example .env
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt
```

Install frontend dependencies:

```bash
cd frontend
npm ci
cd ..
```

The `.env` file is used for local application configuration. Never commit API keys or other credentials.

## Run the Application

Start the complete application:

```bash
docker compose up --build -d --wait
```

- Frontend: http://localhost:8080
- Backend API documentation: http://localhost:8000/docs

Stop the application:

```bash
docker compose down
```

## Tests

### Agent

From the repository root:

```bash
python3 -m unittest discover -s agent/tests -v
python3 -m agent.tests.integration --build-images
```

The integration suite executes real pytest and Vitest cases inside Docker using deterministic model-response fixtures.

### Backend

From the `backend` directory, with the Python environment activated:

```bash
python -m pytest
python -m pytest --cov=app --cov-report=term-missing
```

### Frontend

From the `frontend` directory:

```bash
npm test
npm run test:coverage
npm run build
```

## GitHub Actions Setup

The workflow uses two GitHub Actions definitions:

- [PR Agent Request](.github/workflows/pr-agent.yml) — detects pull request events.
- [Unit Test Agent](.github/workflows/publish-results.yml) — analyzes changes, generates tests, verifies results, and publishes them.

Configure the repository:

1. Enable GitHub Actions in **Settings → Actions → General**.
2. Keep default workflow permissions read-only and enable **Allow GitHub Actions to create and approve pull requests**.
3. Create an environment named `unit-test-agent` under **Settings → Environments**.
4. Restrict environment deployment to the exact default branch using **Selected branches and tags → Branch → main** (or the actual default branch).
5. Add environment secret `OPENAI_API_KEY`.
6. Add environment variable `OPENAI_MODEL` using a model that supports Responses API strict Structured Outputs.
7. Protect the default branch against unreviewed changes to workflows and agent controls.

Do not store the OpenAI key as a repository-level secret.

### Automatic Execution

Open or update a same-repository pull request containing application code changes.

The workflow automatically:

1. Analyzes changed code.
2. Selects the relevant skills.
3. Generates or updates tests when needed.
4. Runs tests in isolated Docker containers.
5. Reviews the results.
6. Publishes a report on the original PR.
7. Creates a draft PR with verified tests.

PRs from forks, managed `test-agent/` branches, and sensitive configuration changes are restricted or require human review.

### Manual Execution

Go to **Actions → Unit Test Agent → Run workflow**.

Select the default branch and provide the number of an open PR.

This runs a new analysis against the selected PR and may incur OpenAI API costs.

## Security

Pull request content is treated as untrusted input. The agent cannot execute arbitrary model-provided commands or modify production files.

Test execution runs in restricted Docker containers without network access or model credentials. PR identities, commit SHAs, generated file paths, and artifacts are validated independently.

The agent does not automatically merge pull requests.

Key implementation files:

- [Agent instructions](agent/AGENT.md)
- [Input and file safety](agent/safety.py)
- [Test execution isolation](agent/verification.py)
- [Artifact validation](agent/state.py)
- [GitHub publication](agent/publish.py)
- [GitHub Actions workflow](.github/workflows/publish-results.yml)

For security trade-offs and remaining findings, see [SOLUTION.md](SOLUTION.md#security) and [docs/validation.md](docs/validation.md).

## Documentation

- [SOLUTION.md](SOLUTION.md) — architecture, skills, design decisions, live demonstration, and limitations.
- [docs/validation.md](docs/validation.md) — local test results, dependency audits, container scans, and remaining risks.
- [agent/AGENT.md](agent/AGENT.md) — agent instructions and constraints.
- [skills/](skills/) — reusable testing skills.