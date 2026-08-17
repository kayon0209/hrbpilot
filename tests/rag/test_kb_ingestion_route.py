"""The ingestion worker must not race the transaction that creates its task."""

from types import SimpleNamespace

from starlette.requests import Request

from app.access.routes import kb as kb_routes


class _Result:
    def __init__(self, value) -> None:
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalars(self):
        return self

    def all(self):
        return self.value


class _Session:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.results = [
            _Result(SimpleNamespace(id="kb-1")),
            _Result(["doc-1"]),
        ]

    async def execute(self, stmt):
        return self.results.pop(0)

    def add(self, obj) -> None:
        self.task = obj

    async def flush(self) -> None:
        self.events.append("flush")

    async def commit(self) -> None:
        self.events.append("commit")


async def test_trigger_commits_task_before_worker_starts(monkeypatch) -> None:
    events: list[str] = []
    session = _Session(events)
    request = Request({"type": "http", "method": "POST", "path": "/"})
    request.state.tenant_id = "tenant-a"
    request.state.user_id = "user-a"
    request.state.user_role = "hr_manager"

    def worker(task_id: str, tenant_id: str) -> None:
        events.append("worker")

    monkeypatch.setattr(kb_routes, "dispatch_ingestion_task", worker)

    await kb_routes.trigger_ingestion("kb-1", request, session)

    assert events.index("commit") < events.index("worker")
