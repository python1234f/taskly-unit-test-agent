# Solution — AI Unit Test Agent

## Overview

Taskly extends the original React/TypeScript and FastAPI application with a custom AI agent that analyzes GitHub pull requests, generates or updates unit tests, verifies them, and publishes results directly to the originating PR.

The solution uses Python 3.12, OpenAI Responses API, four reusable skills, GitHub Actions, and isolated Docker test execution.

## Architecture and Technical Decisions

The workflow consists of five stages:

1. **Prepare:** Validate the PR, repository, commit SHA, and changed files.
2. **Generate:** Analyze changes, select skills, retrieve context, and propose tests.
3. **Verify:** Run the baseline, generated tests, and affected test suites in Docker.
4. **Review:** Evaluate results and attempt up to two technical repairs if necessary.
5. **Publish:** Post a report to the original PR and create a separate draft PR containing verified tests.

Implementation:

- [Agent orchestration](agent/engine.py)
- [OpenAI client](agent/llm_client.py)
- [Context retrieval](agent/context.py)
- [Test verification](agent/verification.py)
- [GitHub publication](agent/publish.py)
- [GitHub Actions workflow](.github/workflows/publish-results.yml)

The controller uses strict JSON Schema responses and independently validates model output. The model cannot execute arbitrary commands, modify production code, or approve its own pull requests.

Test execution is separated from model access and publication to limit credential exposure.

## Custom Agent and Reusable Skills

The agent's operating instructions are defined in [agent/AGENT.md](agent/AGENT.md).

Four reusable skills are loaded on demand:

| Skill | Purpose |
|---|---|
| [analyze-pr](skills/analyze-pr/SKILL.md) | Analyze PR changes and identify missing coverage |
| [backend-testing](skills/backend-testing/SKILL.md) | Generate pytest/FastAPI tests |
| [frontend-testing](skills/frontend-testing/SKILL.md) | Generate React/Vitest tests |
| [verify-tests](skills/verify-tests/SKILL.md) | Assess test execution and technical repairs |

Skill selection is performed by the model. The controller records the name, selection reason, execution stage, and SHA-256 hash of each loaded skill.

The agent can return `GENERATE_TESTS`, `UPDATE_TESTS`, `NO_TESTS_NEEDED`, or `NEEDS_HUMAN_REVIEW`.

## Setup and Execution

Requirements:

- Public GitHub repository with GitHub Actions enabled.
- Python 3.12 and Docker for local validation.
- GitHub Actions environment named `unit-test-agent`.
- Environment secret: `OPENAI_API_KEY`.
- Environment variable: `OPENAI_MODEL`, configured with a model supporting Responses API strict Structured Outputs.
- Environment deployment restricted to the default branch, typically `main`.
- Permission for GitHub Actions to create pull requests.

After merging the agent and workflows into `main`, opening or updating a same-repository PR automatically triggers the agent.

Manual execution is also supported through **Actions → Unit Test Agent → Run workflow**, selecting `main` and entering the original PR number.

Local validation:

```bash
python3 -m unittest discover -s agent/tests -v
python3 -m agent.tests.integration --build-images
```

See [README.md](README.md) for detailed setup instructions.

## Live GitHub Demonstration

The workflow was tested with a real OpenAI API call and GitHub-hosted runners.

### Evidence

| Resource | Link |
|---|---|
| Original feature PR | [PR #1 — Task title normalization](https://github.com/python1234f/taskly-unit-test-agent/pull/1) |
| Automatically generated test PR | [Draft PR #2](https://github.com/python1234f/taskly-unit-test-agent/pull/2) |
| Successful end-to-end execution | [GitHub Actions — Successful Run](https://github.com/python1234f/taskly-unit-test-agent/actions/runs/39858909603) |
| Initial execution | [GitHub Actions — Initial Run](https://github.com/python1234f/taskly-unit-test-agent/actions/runs/39855766155) |
| Diagnostic execution | [GitHub Actions — Diagnostic Run](https://github.com/python1234f/taskly-unit-test-agent/actions/runs/39857363621) |

### Scenario

[PR #1](https://github.com/python1234f/taskly-unit-test-agent/pull/1) introduces `normalize_title()` in `backend/app/title_utils.py` without accompanying unit tests.

The agent:

1. Detected missing tests and selected `GENERATE_TESTS`.
2. Loaded `analyze-pr` and `backend-testing`.
3. Generated two pytest tests in `backend/tests/test_title_utils.py`.
4. Executed the tests inside an isolated Docker container.
5. Loaded `verify-tests` and assessed the results.
6. Returned `assessment: ACCEPT` and final status `VERIFIED`.
7. Published the results and created [Draft PR #2](https://github.com/python1234f/taskly-unit-test-agent/pull/2).

### Results

| Metric | Result |
|---|---|
| Existing backend tests | 38 passed |
| Generated tests | 2 passed |
| Full backend suite | 40 passed |
| Backend coverage | 70.66% → 71.86% |
| Coverage improvement | +1.20 percentage points |
| Verification | `PASSED` |
| Assessment | `ACCEPT` |
| Final status | `VERIFIED` |
| Repair attempts | 0 |

The successful execution completed in approximately **2 minutes 50 seconds**.

### Reliability Finding

An earlier execution returned `NO_TESTS_NEEDED` during quality assessment, despite successfully generating and executing tests.

The controller rejected the inconsistent response and required human review rather than falsely reporting success.

The assessment instructions were clarified to distinguish the initial PR decision from the review of already-generated tests. The subsequent execution returned `GENERATE_TESTS`, `ACCEPT`, and `VERIFIED`.

## Security

The agent treats PR content as untrusted data and applies multiple security controls:

- **Prompt injection resistance:** PR content cannot override agent instructions.
- **Least privilege:** Model access, test execution, and GitHub publication use separate jobs.
- **Credential isolation:** Generated tests run without OpenAI credentials.
- **Sandboxing:** Docker containers have no network access, run as non-root, and use restricted filesystems, capabilities, CPU, memory, and execution time.
- **File safety:** Generated changes are restricted to approved test paths and operations.
- **Artifact integrity:** PR identities, commit SHAs, file hashes, and execution artifacts are validated.
- **Test integrity:** Assertions and behavioral expectations cannot be weakened during automatic repair.
- **Human oversight:** Unverified tests are not automatically merged.

Relevant implementations: [agent/safety.py](agent/safety.py), [agent/state.py](agent/state.py), [agent/verification.py](agent/verification.py), and [GitHub Actions](.github/workflows/publish-results.yml).

Local security and dependency checks are documented in [docs/validation.md](docs/validation.md). Final container scans reported no CRITICAL findings, although some unfixed Debian HIGH findings remain.

## Assumptions and Limitations

- Automatic execution targets small pull requests originating from the same repository.
- Forks, sensitive configuration changes, unsupported cases, and oversized inputs require human review.
- Test updates append cases rather than replacing existing tests.
- The live GitHub demonstration covers the backend; frontend test generation and execution have been validated with local replay scenarios.
- The two-repair limit prevents unbounded attempts.
- Docker provides practical isolation but does not offer the same boundary as a dedicated virtual machine.
- Passing tests and LLM assessment do not formally prove behavioral correctness.
- Generated test PRs require human review and are never merged automatically.

## Production Improvements

For production deployment, I would add stronger VM-level isolation, more extensive adversarial and mutation testing, provider-side cost controls, automated cleanup of obsolete draft PRs, and broader context retrieval for larger codebases.

Detailed local test results, dependency scans, and remaining risks are available in [docs/validation.md](docs/validation.md).