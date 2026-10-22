from datetime import date, datetime, timezone
from unittest.mock import Mock, patch

from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.features.todos.models import TodoRecord
from app.features.todos.repository import TodoRepository
from app.features.todos.schemas import TodoCreate, TodoUpdate


def test_list_maps_models_and_orders_tasks_newest_first(model_factory, todo_values, todo):
    session = Mock(spec=Session)
    session.scalars.return_value = [model_factory(todo_values)]
    assert TodoRepository(session).list() == [todo]
    statement = session.scalars.call_args.args[0]
    expected = select(TodoRecord).order_by(TodoRecord.created_at.desc(), TodoRecord.id.desc())
    assert statement.compare(expected)
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "ORDER BY todos.created_at DESC, todos.id DESC" in compiled


def test_get_returns_none_for_missing_task():
    session = Mock(spec=Session)
    session.get.return_value = None
    assert TodoRepository(session).get(404) is None
    session.get.assert_called_once_with(TodoRecord, 404)


def test_create_persists_defaults_and_returns_refreshed_values():
    session = Mock(spec=Session)
    session.refresh.side_effect = lambda record: setattr(record, "id", 8)
    now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
    with patch("app.features.todos.repository.datetime") as clock:
        clock.now.return_value = now
        saved = TodoRepository(session).create(TodoCreate(title="  New task  "))
    record = session.add.call_args.args[0]
    assert saved.id == 8
    assert saved.title == "New task"
    assert saved.description == ""
    assert saved.priority == "medium"
    assert saved.completed is False
    assert saved.created_at == saved.updated_at == now
    session.commit.assert_called_once_with()
    session.refresh.assert_called_once_with(record)


def test_get_returns_existing_task(model_factory, todo_values, todo):
    session = Mock(spec=Session)
    session.get.return_value = model_factory(todo_values)
    assert TodoRepository(session).get(todo.id) == todo


def test_partial_update_preserves_omitted_fields_and_clears_due_date(model_factory, todo_values):
    record = model_factory({**todo_values, "due_date": date(2026, 10, 25)})
    session = Mock(spec=Session)
    session.get.return_value = record
    now = datetime(2026, 10, 22, 12, tzinfo=timezone.utc)
    with patch("app.features.todos.repository.datetime") as clock:
        clock.now.return_value = now
        saved = TodoRepository(session).update(record.id, TodoUpdate(completed=True, due_date=None))
    assert saved.completed is True
    assert saved.due_date is None
    assert saved.title == todo_values["title"]
    assert saved.description == todo_values["description"]
    assert saved.created_at == todo_values["created_at"]
    assert saved.updated_at == now
    session.commit.assert_called_once_with()
    session.refresh.assert_called_once_with(record)


def test_update_missing_task_does_not_commit():
    session = Mock(spec=Session)
    session.get.return_value = None
    assert TodoRepository(session).update(404, TodoUpdate(title="Updated")) is None
    session.commit.assert_not_called()
    session.refresh.assert_not_called()


def test_delete_existing_task_commits_once(model_factory, todo_values):
    record = model_factory(todo_values)
    session = Mock(spec=Session)
    session.get.return_value = record
    assert TodoRepository(session).delete(record.id) is True
    session.delete.assert_called_once_with(record)
    session.commit.assert_called_once_with()
