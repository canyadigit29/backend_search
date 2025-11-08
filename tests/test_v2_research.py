import types
from fastapi.testclient import TestClient

from app.api.v2.research import router as research_router
import app.api.v2.research as research_module
from fastapi import FastAPI


class FakeSupabaseTable:
    def __init__(self, name):
        self.name = name
        self._filters = {}
        self._order = None
        self._single = False

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def order(self, *args, **kwargs):
        self._order = args
        return self

    def maybe_single(self):
        return self

    def single(self):
        return self

    def insert(self, payload):
        return types.SimpleNamespace(execute=lambda: types.SimpleNamespace(data=[{"id": "rep_1", **payload}]))

    def execute(self):
        if self.name == "workspaces":
            return types.SimpleNamespace(data={"id": self._filters.get("id"), "user_id": "u1", "instructions": "Be precise."})
        if self.name == "workspace_vector_stores":
            return types.SimpleNamespace(data={"vector_store_id": "vs_test_v2"})
        if self.name == "research_reports":
            if self._filters.get("workspace_id"):
                return types.SimpleNamespace(data=[{"id": "rep_1", "question": "Q", "created_at": "2025-11-08"}])
            if self._filters.get("id"):
                return types.SimpleNamespace(data={"id": "rep_1", "question": "Q", "draft": "D"})
        return types.SimpleNamespace(data=None)


class FakeSupabaseClient:
    def table(self, name):
        return FakeSupabaseTable(name)


class FakeStreamEvent:
    def __init__(self, type, delta=None, error=None):
        self.type = type
        self.delta = delta
        self.error = error


class FakeResponses:
    def stream(self, **kwargs):
        yield FakeStreamEvent('response.output_text.delta', delta='First chunk ')
        yield FakeStreamEvent('response.output_text.delta', delta='Second chunk')
    def stream_with_error(self, **kwargs):
        yield FakeStreamEvent('response.output_text.delta', delta='Start ')
        yield FakeStreamEvent('response.error', error={'message': 'Boom'})

    def create(self, **kwargs):
        out = [types.SimpleNamespace(type='output_text', text='Non-stream draft')]
        return types.SimpleNamespace(id='resp_2', output=out)


class FakeOpenAI:
    def __init__(self):
        self.responses = FakeResponses()


def build_app(monkeypatch):
    app = FastAPI()
    app.include_router(research_router, prefix="/api/v2")
    monkeypatch.setattr(research_module, "get_supabase_client", lambda: FakeSupabaseClient())
    monkeypatch.setattr(research_module, "OpenAI", lambda: FakeOpenAI())
    return app


def test_research_streaming(monkeypatch):
    app = build_app(monkeypatch)
    client = TestClient(app)
    body = {"workspace_id": "ws_1", "question": "What happened?", "stream": True}
    r = client.post("/api/v2/research", json=body)
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("text/event-stream")
    # ensure event stream contains complete event
    assert "event: complete" in r.text


def test_research_non_streaming(monkeypatch):
    app = build_app(monkeypatch)
    client = TestClient(app)
    body = {"workspace_id": "ws_1", "question": "What happened?", "stream": False}
    r = client.post("/api/v2/research", json=body)
    assert r.status_code == 200
    j = r.json()
    assert "draft" in j and j["draft"] == "Non-stream draft"


def test_research_stream_error(monkeypatch):
    app = build_app(monkeypatch)
    # Force stream to emit error event
    class ErrClient:
        def __init__(self):
            self.responses = types.SimpleNamespace(
                stream=lambda **kw: FakeResponses().stream_with_error(),
                create=FakeResponses().create,
            )
    monkeypatch.setattr(research_module, "OpenAI", lambda: ErrClient())
    client = TestClient(app)
    body = {"workspace_id": "ws_1", "question": "Q?", "stream": True}
    r = client.post("/api/v2/research", json=body)
    assert r.status_code == 200
    text = r.text
    # SSE stream should contain error event with message
    assert "event: error" in text
    assert "Boom" in text