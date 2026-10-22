import pytest

from app.features.todos.schemas import TodoCreate, TodoUpdate


def test_list_returns_tasks_from_repository(client, repository_mock, todo):
    repository_mock.list.return_value = [todo]
    response = client.get("/api/todos")
    assert response.status_code == 200
    assert response.json() == [todo.model_dump(mode="json")]
    repository_mock.list.assert_called_once_with()


def test_create_trims_input_and_returns_created_task(client, repository_mock, todo):
    repository_mock.create.return_value = todo
    response = client.post("/api/todos", json={"title": "  Plan the sprint  ", "priority": "high"})
    assert response.status_code == 201
    assert response.json() == todo.model_dump(mode="json")
    repository_mock.create.assert_called_once_with(
        TodoCreate(title="Plan the sprint", priority="high")
    )


@pytest.mark.parametrize("payload", [
    {"title": "   "},
    {"title": "x" * 121},
    {"title": "Task", "priority": "urgent"},
    {"title": "Task", "due_date": "2026-02-30"},
])
def test_invalid_create_never_calls_repository(client, repository_mock, payload):
    assert client.post("/api/todos", json=payload).status_code == 422
    repository_mock.create.assert_not_called()


def test_get_returns_the_requested_task(client, repository_mock, todo):
    repository_mock.get.return_value = todo
    response = client.get(f"/api/todos/{todo.id}")
    assert response.status_code == 200
    assert response.json() == todo.model_dump(mode="json")
    repository_mock.get.assert_called_once_with(todo.id)


def test_patch_passes_only_explicit_changes(client, repository_mock, todo):
    saved = todo.model_copy(update={"completed": True})
    repository_mock.update.return_value = saved
    response = client.patch(f"/api/todos/{todo.id}", json={"completed": True})
    assert response.status_code == 200
    assert response.json() == saved.model_dump(mode="json")
    repository_mock.update.assert_called_once_with(todo.id, TodoUpdate(completed=True))


def test_delete_returns_no_response_body(client, repository_mock, todo):
    repository_mock.delete.return_value = True
    response = client.delete(f"/api/todos/{todo.id}")
    assert response.status_code == 204
    assert response.content == b""
    repository_mock.delete.assert_called_once_with(todo.id)


@pytest.mark.parametrize("method", ["get", "patch", "delete"])
def test_missing_task_returns_404(client, repository_mock, method):
    repository_mock.get.return_value = None
    repository_mock.update.return_value = None
    repository_mock.delete.return_value = False
    options = {"json": {"title": "Updated"}} if method == "patch" else {}
    response = getattr(client, method)("/api/todos/404", **options)
    assert response.status_code == 404
    assert response.json() == {"detail": "Task not found."}


@pytest.mark.parametrize("payload", [{}, {"title": None}, {"completed": 1}, {"description": "x" * 2001}])
def test_invalid_patch_never_calls_repository(client, repository_mock, payload):
    assert client.patch("/api/todos/7", json=payload).status_code == 422
    repository_mock.update.assert_not_called()
