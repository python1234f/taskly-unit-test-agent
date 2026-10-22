# Validation

Recorded locally on 2026-10-22. No remote branch, commit, PR, or comment was created. No live OpenAI API call was made.

## Application tests

| Suite | Baseline | After additions | Coverage metric | Before | After |
|---|---:|---:|---|---:|---:|
| Frontend | 18 passed | 31 passed | Lines | 47.68% | 71.52% |
| Backend | 21 passed | 38 passed | Lines and branches combined | 42.42% | 71.52% |

Backend statement coverage changed from 44.14% to 69.66%, and branch coverage from 30% to 85%. Frontend branch coverage changed from 43.79% to 61.31%. Coverage exclusions and production application code were not changed. The pre-addition baseline was also reproduced with the updated dependencies.

Backend tests ran on Python 3.12.15 in Docker without PostgreSQL. Frontend tests/build ran on Node 24 locally; agent integration tests execute on Node 22. The existing FastAPI/Starlette deprecation warning about `httpx` remains visible; no warning suppression or unrelated client migration was introduced.

`npm run build`, `npm run build-storybook`, and the production frontend Docker build passed. Storybook initially failed under the terminal's network-interface restriction and passed when rerun outside that restriction. Storybook retains its large-preview-chunk warning.

## Agent tests

`python -m unittest discover -s agent/tests -v`: **36 passed**, using deterministic model responses and fake API transport. `python -m agent.tests.integration`: **5 passed**, using fixture model responses and actual Docker test execution. Four skill files passed the skill validator; both workflows passed `actionlint` 1.7.7.

| Case | Verification performed |
|---|---|
| Backend PR | Deterministic decision/skill tests and real pytest execution of a case appended to an existing file |
| Frontend PR | Deterministic decision/skill tests and real React/Testing Library/Vitest execution |
| Documentation only | Replay returns `NO_TESTS_NEEDED`; verification is not called |
| Existing coverage | Unit test supplies changed source and an existing matching test; no proposal is produced |
| Generated failure | Real generated-test import failure, one fixture-guided repair, successful rerun; separate unit test enforces the two-repair ceiling |
| Updated PR | Fake GitHub API changes head SHA; publication is rejected before any write |
| Prompt injection | Untrusted diff/context requests and diagnostics cannot supply commands or read disallowed files; descriptions are excluded from model input; this is not a live-model robustness claim |
| Model unavailable | Transport failure and exhausted replay responses produce controlled errors without leaking transport details |
| Invalid paths | Traversal, absolute paths, forbidden directories, symlinks, and unsupported operations rejected |
| Assertion weakening | Changed/removed assertions or case names rejected; conflicting existing setup cannot be appended |
| Duplicate prevention | Fake REST publication repeated twice: one branch, one draft PR, one report comment |
| Artifact validation | Wrong run, digest, state identity, extra ZIP files, traversal, symlink, failed/empty execution evidence rejected |

The real runtime probe checked a non-root UID, absence of host credential sentinels and Docker-configured proxy values, no Docker socket or private input, a read-only root and source mount, blocked source-file writes, zero effective capabilities, no-new-privileges, cgroup CPU/memory/PID limits, and failed external network access.

Final local replay results:

| Scenario | Decision | Actual execution |
|---|---|---|
| Backend | `UPDATE_TESTS` | 38 baseline cases; 8 targeted cases including one appended case; 39 full-suite cases passed |
| React frontend | `GENERATE_TESTS` | 31 baseline cases; one generated case; 32 full-suite cases passed |
| Documentation | `NO_TESTS_NEEDED` | No test runner invoked |
| Import repair | `UPDATE_TESTS` | Initial generated case failed; one repair preserved its assertions; rerun passed |

The backend replay measured 71.52% → 72.73% combined coverage on the same production snapshot. The React replay kept line coverage at 71.52% while verifying busy-state behavior: passing a useful new case does not imply increased line coverage. Both recovery patches passed `git apply --check` without being applied to the application working tree.

These checks do not replace a live model run or GitHub-hosted end-to-end run. Both remain outstanding.

## Dependency audit

- `npm ci` succeeded with the updated lockfile. `npm audit --json`: zero reported vulnerabilities.
- OSV query of all 349 frontend lockfile entries: no matches.
- Backend `pip check`: no broken requirements. `pip-audit` on the complete pinned requirement list: no known vulnerabilities found. Audit tooling ran separately from the application environment.
- Storybook fixes: `GHSA-f2ph-cmg5-wxc8`, `GHSA-px85-f4q5-jh77`.
- Vitest mocker fixes: `GHSA-qxg6-89mg-rqv8`, `GHSA-xp26-3454-mjgp`.
- Vite fixes: `GHSA-rq7h-c2jc-7f22`, `GHSA-9jrq-w75r-8gcw`, `GHSA-698v-4r3x-3vcv`, `GHSA-r825-w8mm-5pxw`, `GHSA-587h-7wmj-h5pm`.
- Backend fixes: `GHSA-6q48-6c7w-9gw2` / `PYSEC-2026-4320` are one certifi issue; `GHSA-xcq7-p3pf-p88h` affects OpenTelemetry parsing.

## Container scan scope

Trivy scanned the pinned Python base and the initially selected Node Bookworm base, including installed library packages. The first scan ran out of temporary space; a retry with a dedicated cache completed. The Python base had 44 HIGH package/CVE records (8 distinct CVEs), no CRITICAL records, and no supplied fixed versions. The initial Node base had 67 HIGH and one CRITICAL record, including fixable dependencies bundled with npm/Yarn.

The CRITICAL `CVE-2023-45853` zlib source finding has a [Debian qualification](https://security-tracker.debian.org/tracker/CVE-2023-45853): the vulnerable MiniZip code is not built into the affected Bookworm binary package. It is not silently ignored. The test image uses the newer Trixie base, and removes npm/Corepack/Yarn after installation. The application frontend builder upgrades npm to 11.17.0; it is absent from the final nginx stage.

Scanning the complete frontend image also found Go standard-library issues in esbuild 0.25.12's published binary, including `CVE-2025-68121`. Docker builds now compile the same upstream version with Go 1.26.10 from a pinned builder image, avoiding an unsupported esbuild/Vite version override. Production Docker build and agent integration tests passed with the rebuilt compiler. Host npm installations still use the upstream binary; the additional native-library remediation applies to the Docker execution path.

| Final test image | HIGH package/CVE records | CRITICAL | Fixed version supplied by feed | HIGH/CRITICAL language-package findings |
|---|---:|---:|---|---:|
| Frontend | 43 | 0 | None | 0 |
| Backend | 44 | 0 | None | 0 |

The eight remaining distinct Debian CVEs are `CVE-2026-76642`, `CVE-2026-78408`, `CVE-2026-78409`, `CVE-2026-78410`, `CVE-2026-54369`, `CVE-2025-69720`, `CVE-2026-16742`, and `CVE-2026-9538`. Multiple binary packages from a source package produce repeated records. Scans used HIGH/CRITICAL severity scope without ignore rules; lower-severity image findings are not quantified here.

Unfixed Debian HIGH findings remain a known limitation. No arbitrary removal of essential OS libraries or severity suppression is used. Containers have no secrets or network during tests and do not gain privileges; this reduces exposure without claiming that the findings are fixed. Production nginx/PostgreSQL images have not been comprehensively audited by this change.

## Reproduce

Use the application commands and Docker integration command in README. Audit the pinned backend list with `python -m pip_audit -r backend/requirements.txt` in a separate tooling environment. Local replay outputs and raw scanner reports are ignored under `.agent-output`; they are not fabricated hosted artifacts.
