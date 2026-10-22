---
name: backend-testing
description: Generate pytest unit tests for Taskly FastAPI endpoints, Pydantic validation and synchronous SQLAlchemy repository behavior.
---

# Backend unit tests

Use existing backend/tests/conftest.py fixtures and tests/mocks.py. The client fixture mounts only the todo router and overrides get_repository. It does not execute the application factory, lifespan, or health router. Use an explicit fixture for those boundaries rather than assuming the production app is already running.

TodoRepository uses synchronous sqlalchemy.orm.Session. Mock or autospec Session, not AsyncSession. Exercise returned data and state transitions; verify commit, refresh, rollback or close when those operations define the contract. Do not require PostgreSQL for ordinary unit cases, and do not substitute SQLite into the PostgreSQL engine helper without handling its driver-specific connect_args.

Pydantic v2 distinguishes omitted patch fields from explicit null through model_fields_set and model_dump(exclude_unset=True). due_date may be cleared; the other writable fields may not be null. completed is a strict boolean. Check success and failure boundaries with pytest.raises or the HTTP status and response body. Preserve original assertions and avoid duplicating existing parameterized cases.

Generate test_ functions with unique names and deterministic dates. Use existing fixtures or uniquely named fixture_ helpers decorated with pytest.fixture. Import application code through app.*, matching the existing pytest configuration. Avoid wall-clock equality; assert timestamp relationships or freeze the relevant clock with a mock.
