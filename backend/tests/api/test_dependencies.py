from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from starlette.requests import Request

from app.api.dependencies import get_repository


def test_session_creation_failure_propagates_from_repository_dependency():
    session_factory = Mock(side_effect=RuntimeError("Session unavailable"))
    app = SimpleNamespace(state=SimpleNamespace(session_factory=session_factory))
    request = Request({"type": "http", "app": app})
    dependency = get_repository(request)
    with pytest.raises(RuntimeError, match="Session unavailable"):
        next(dependency)
    session_factory.assert_called_once_with()


def test_successful_dependency_closes_session_without_rollback():
    session = Mock()
    app = SimpleNamespace(state=SimpleNamespace(session_factory=Mock(return_value=session)))
    dependency = get_repository(Request({"type": "http", "app": app}))
    repository = next(dependency)
    assert repository.session is session
    with pytest.raises(StopIteration):
        next(dependency)
    session.close.assert_called_once_with()
    session.rollback.assert_not_called()


def test_failed_dependency_rolls_back_and_closes_session():
    session = Mock()
    app = SimpleNamespace(state=SimpleNamespace(session_factory=Mock(return_value=session)))
    dependency = get_repository(Request({"type": "http", "app": app}))
    next(dependency)
    with pytest.raises(RuntimeError, match="Write failed"):
        dependency.throw(RuntimeError("Write failed"))
    session.rollback.assert_called_once_with()
    session.close.assert_called_once_with()
