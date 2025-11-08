import types
from fastapi.testclient import TestClient

from app.api.v2.chat import router as chat_router
import app.api.v2.chat as chat_module
from fastapi import FastAPI


class FakeSupabaseTable:
    def __init__(self, name):
        self.name = name
        self._filters = {}
        self._order = None
        self._single = False
        self._maybe_single = False
        self._insert_payload = None

    def select(self, *args, **kwargs):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def single(self):
        self._single = True
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    def order(self, *args, **kwargs):
        self._order = args
        return self

    def insert(self, payload):
        self._insert_payload = payload
        return types.SimpleNamespace(execute=lambda: types.SimpleNamespace(data=[{"id": "rep_1", **payload}]))

    def execute(self):
        if self.name == "workspaces":
            return types.SimpleNamespace(data={"id": self._filters.get("id"), "user_id": "test-user-id", "instructions": "Be helpful."})
        if self.name == "workspace_vector_stores":
            return types.SimpleNamespace(data={"vector_store_id": "vs_test_v2"})
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
        yield FakeStreamEvent('response.output_text.delta', delta='Hello ')
        yield FakeStreamEvent('response.output_text.delta', delta='world')

    def stream_with_error(self, **kwargs):
        yield FakeStreamEvent('response.output_text.delta', delta='Partial ')
        yield FakeStreamEvent('response.error', error={'message': 'Injected failure'})

    def create(self, **kwargs):
        out = [types.SimpleNamespace(type='output_text', text='Answer from v2')]
        return types.SimpleNamespace(id='resp_1', output=out)


class FakeOpenAI:
    def __init__(self):
        self.responses = FakeResponses()


def build_app(monkeypatch):
    app = FastAPI()
    app.include_router(chat_router, prefix="/api/v2")
    monkeypatch.setattr(chat_module, "get_supabase_client", lambda: FakeSupabaseClient())
    monkeypatch.setattr(chat_module, "OpenAI", lambda: FakeOpenAI())
    # ensure feature flag defaults allow the route
    return app


def test_chat_streaming(monkeypatch):
    app = build_app(monkeypatch)
    client = TestClient(app)
    body = {
        "workspace_id": "ws_1",
        "chat_id": "ch_1",
        "input": "Hi",
        "stream": True
    }
    r = client.post("/api/v2/chat/respond", json=body)
    assert r.status_code == 200
    assert r.headers.get("content-type", "").startswith("text/plain")
    # TestClient buffers streaming into text
    assert "Hello world" in r.text


def test_chat_non_streaming(monkeypatch):
    app = build_app(monkeypatch)
    client = TestClient(app)
    body = {
        "workspace_id": "ws_1",
        "input": "Hi",
        "stream": False
    }
    r = client.post("/api/v2/chat/respond", json=body)
    assert r.status_code == 200
    j = r.json()
    assert j["text"] == "Answer from v2"


def test_chat_stream_error(monkeypatch):
    app = build_app(monkeypatch)
    # Patch OpenAI responses.stream to emit an error event mid-stream
    class ErrClient:
        def __init__(self):
            self.responses = types.SimpleNamespace(
                stream=lambda **kw: FakeResponses().stream_with_error(),
                create=FakeResponses().create,
            )
    monkeypatch.setattr(chat_module, "OpenAI", lambda: ErrClient())
    client = TestClient(app)
    body = {"workspace_id": "ws_1", "chat_id": "ch_1", "input": "Hi", "stream": True}
    r = client.post("/api/v2/chat/respond", json=body)
    assert r.status_code == 200
    txt = r.text
    assert "Partial" in txt
    assert "[error] Injected failure" in txt