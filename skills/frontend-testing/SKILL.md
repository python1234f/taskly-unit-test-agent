---
name: frontend-testing
description: Generate React 19 and TypeScript tests using Vitest 4, Testing Library, React Query and styled-components in Taskly.
---

# Frontend unit tests

Follow existing .test.ts and .test.tsx conventions with explicit Vitest imports. vi.mock is hoisted: do not reference ordinary top-level variables from a mock factory. Use vi.hoisted when needed, or vi.mocked after imports. Restore spies and globals after each case.

Use userEvent.setup and await interactions. Query by role and accessible name; use findBy or waitFor for asynchronous updates. Assert the outcome, including failure feedback and disabled operations, rather than component internals.

Taskly exports AppProviders from src/app/AppProviders.tsx; it accepts an optional QueryClient. Use a fresh QueryClient with retries disabled per test when exercising real hooks. Clear it after execution. For styled components use AppProviders or the actual ThemeProvider and theme. The existing design-system/mocks.tsx is available for isolated parent behavior, but it cannot establish the real design system's accessibility.

Mutation hooks cancel pending list queries and replace cache data with confirmed server responses. Test those effects through a real QueryClient with a mocked API boundary. Do not mock the hook under test. Account for missing cache and rejected mutations.

Control time with vi.useFakeTimers and vi.setSystemTime for useNotice and isOverdue, then restore real timers. Stub fetch for HTTP tests and exercise 422, 204, server errors and abort behavior. Generate ordinary literal it/test names so the driver can match them to execution results. Do not add duplicate imports or global mocks when appending to an existing file; choose a new file if setup conflicts.
