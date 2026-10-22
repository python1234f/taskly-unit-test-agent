# Solution

## Architecture

One Python 3.12 controller coordinates a bounded OpenAI Responses API conversation. It uses standard-library HTTP/JSON clients, four instruction skills, and Docker for test execution. No framework, server, persistent database, or additional agent is involved.

`pr-agent.yml` sends a PR request with read-only permissions and no model secrets. `publish-results.yml` runs trusted code from the default-branch SHA, obtains PR data through REST, and separates generation, verification, assessment/repair, and publication into different jobs. The explicit three verification rounds avoid moving model credentials into the test runner. Unneeded rounds are skipped.

## Custom Agent

`agent/AGENT.md` defines authority and testing constraints. `engine.py` handles selection, analysis, generation, and assessment. `llm_client.py` calls Responses API with strict `text.format` JSON Schema and validates the answer independently. The model can request allowlisted files and propose `create` or `append` test operations; it cannot submit executable commands.

The model chooses `GENERATE_TESTS`, `UPDATE_TESTS`, `NO_TESTS_NEEDED`, or human review. Controller failures are separate from model decisions and test results. The driver owns PR/run identity, SHA, skill records, file hashes, execution status, and token counters. Decisions about unsupported controls/dependency changes are explicitly policy decisions before model execution. PR descriptions and comments are not fed to the model.

## Reusable Skills

The model first receives only the catalog in `skills.py`. Requested instructions are then read from `skills/<name>/SKILL.md` at the relevant stage:

| Skill | Actual use |
|---|---|
| `analyze-pr` | Compare behavior with related tests and request missing context |
| `backend-testing` | pytest, FastAPI, Pydantic v2, synchronous SQLAlchemy sessions |
| `frontend-testing` | React 19, Vitest 4, Testing Library, providers and cache behavior |
| `verify-tests` | Evaluate execution and distinguish technical repair from an oracle conflict |

Each read records the name, stage, selection reason, and SHA-256 of the loaded instructions. Unloaded skills are not reported as used. Testing skills are not loaded for documentation-only decisions.

## Context Retrieval

`context.py` collects full changed sources, nearby tests, Python/TypeScript imports, pytest fixtures, mocks, providers, and test configuration. Python AST inspection identifies symbols without importing code. Relative imports are followed to a bounded depth; model requests can retrieve additional allowlisted files. Assessment receives relevant source context again and a bounded diagnostic summary.

Automatic limits include 100 PR files, 12 changed source files, 16 KB of diff, 24 context files, 48 KB of selected context, and eight additional file requests. Required context that cannot fit causes controlled review rather than a claim of adequate coverage.

## PR Workflow

The source workflow run, PR number, repository, head/base SHAs, and current controller run/attempt are resolved through GitHub API. Forks and managed branches are excluded. Generation reads git blobs by SHA as text; it never checks out or imports PR code on a credentialed runner.

Verification prepares a bounded source snapshot with trusted dependency/test configuration. Each affected component runs a baseline, selected generated files, and the full affected suite. Frontend execution also runs TypeScript checking. Exit codes and JUnit records must show that each proposed case actually passed; zero cases cannot pass. Coverage is compared only on the same production snapshot and configuration with matching denominators.

The publisher validates the execution artifact, rechecks SHA, updates one bot-authored report, and creates a draft PR only for live, verified changes. Its commit has the analyzed head as its sole parent and changes only allowed test paths. A stable per-PR/per-SHA branch prevents duplicate draft PRs; conflicting existing branches are not overwritten. The delivery artifact is uploaded before PR creation and contains exact test contents, a patch, state, and a report.

`GITHUB_TOKEN` PR events can require approval before downstream workflows run. The `test-agent/` guard works independently of that platform behavior. See [GitHub's token rules](https://docs.github.com/en/actions/concepts/security/github_token). Concurrency cancels superseded runs; checks before writes and explicit SHA labels prevent presenting stale results as current.

## Security

| Boundary | Credentials and execution |
|---|---|
| Generation/assessment | Environment-scoped model key; read-only GitHub token; no PR code execution |
| Verification | Separate hosted runner; read-only fetch token stays on the host; no credentials forwarded into test containers |
| Publication | Write token on a separate runner; API/data processing only; no model key or PR code execution |

Tests run without network, as a non-root UID, with a read-only root filesystem and source mount, dropped capabilities, no-new-privileges, 2 CPUs, 2 GiB memory, 256 PIDs, and a 180-second limit per container. Writable locations are temporary scratch, reports, and a bounded frontend dependency/cache tmpfs. No private directories or host Docker socket are mounted. Proxy variables are explicitly cleared so Docker client proxy configuration cannot forward private proxy credentials. Installation uses trusted manifests in secret-free image builds. Docker is a practical isolation boundary with a shared host kernel, not a formally secure sandbox.

Paths, file operations, original/result hashes, JSON types, artifact size, ZIP entries, archive digest, source run, and PR/SHA identity are validated. ZIP archives are read as one JSON member, never extracted. Reports are parsed as bounded data and text is escaped before comments. Untrusted input cannot change the shell commands or their allowed arguments. No cache from PR execution is restored into a privileged job.

`unit-test-agent` must use an exact default-branch **Branch** environment rule. GitHub matches deployment policies to `GITHUB_REF`; `workflow_run` uses the default branch, while PR merge refs do not match that rule. This follows the [environment rules](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments) and [event reference](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows). Hosted enforcement still needs the final integration check. Administrators and approved default-branch workflow changes remain trusted.

Images are pinned by digest. The frontend image removes bundled package managers, and Docker builds rebuild the same esbuild 0.25.12 source with Go 1.26.10 to address native-library findings without overriding Vite's dependency range. Final scans have no CRITICAL findings; unfixed Debian HIGH findings are retained and documented rather than suppressed.

## Self-Healing and the Test Oracle

After execution the model receives `verify-tests` guidance, actual bounded diagnostics as untrusted data, the source context, test proposals, and frozen behavioral expectations. At most two repairs are allowed, each followed by a fresh secret-free verification job. Baseline/infrastructure failures do not trigger speculative production fixes.

Existing tests are preserved byte-for-byte: updates append cases and conservative checks reject conflicting setup or bindings. Repairs must preserve generated case names and assertions. Python assertions, parametrization and literal test data are compared through AST; frontend expectations use conservative source comparison. Changes to expected behavior or assertions require human review. These checks and an LLM review cannot formally establish business correctness or detect every semantic weakening through mocks. No automatic merge occurs.

## Prerequisites

Follow the exact GitHub UI steps in [README.md](README.md#github-actions-setup). Required configuration is a public repository, Actions enabled, a protected default branch, the restricted `unit-test-agent` environment, environment secret `OPENAI_API_KEY`, environment variable `OPENAI_MODEL`, and permission for Actions to create PRs. `GITHUB_TOKEN` is supplied by GitHub; no PAT is required.

There are no API billing retries. The controller caps work at 10 calls, 240 KB of cumulative message input, and 4,096 requested output tokens per call. Usage is recorded when returned by the API. These are technical bounds, not a guaranteed USD limit; the approximate $1/run and $10/demo goals depend on the configured model and provider billing.

## Example Run

No real GitHub PR, live model call, or hosted execution has been performed. There is no genuine PR number, analyzed GitHub SHA, draft URL, or hosted artifact to cite yet.

Local replay uses synthetic PR identities and fixture model responses while executing real pytest and React/Vitest cases. `python -m agent.tests.integration` checks backend generation, React generation, documentation-only decisions, an import failure followed by repair, and actual runtime isolation. Reports and recoverable patches are written under `.agent-output/integration`. Replay results are explicitly labeled and cannot be published through the live publisher. See [validation results](docs/validation.md).

After repository configuration, the acceptance run must open a small same-repository application PR, verify a real model decision and generated test execution, check the SHA-bound comment/artifact/draft PR, then update the PR and exercise the stale-run guard. A README-only PR should produce `NO_TESTS_NEEDED` and no test PR.

## Assumptions and Limitations

- Use fresh GitHub-hosted Linux runners and Docker; the container isolation probes assume cgroup v2. Do not reuse privileged self-hosted hosts for hostile code.
- Automatic operation covers small same-repository PRs. Forks, changed controls/dependencies, oversized context, unsupported filenames, and ambiguous test rewrites require review.
- Tests are created or appended, not rewritten in place. Ordinary pytest functions and literal-name Vitest cases are supported; complex generated test structures are intentionally restricted.
- JUnit/coverage from executed code are evidence, not tamper-proof proof against an actively malicious test process. The credential boundary must hold even if reports are dishonest.
- Existing generated branches are immutable. A rerun proposing different tests for the same SHA leaves an artifact instead of overwriting them. Older-SHA draft PRs are not automatically closed.
- Container scans retain unfixed distribution-level findings; see validation notes. API and hosted integration behavior remain unverified until credentials and repository settings are configured.

## Production Improvements

Use stronger VM isolation for hostile workloads, vendor-reviewed image refreshes and automated rescans, provider-side spending controls, richer semantic/mutation checks for test oracles, and managed cleanup of superseded test PRs. Add broader context retrieval only when actual PRs exceed the current bounded approach.
