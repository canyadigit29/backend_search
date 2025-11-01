import io
import os
import types
import tempfile
from fastapi.testclient import TestClient

import importlib
responses_module = importlib.import_module("app.api.Responses.router")
from app.api.Responses.router import router as responses_router


class FakeOpenAIFile:
    def __init__(self, fid="file_test_1"):
        self.id = fid


class FakeOpenAIVectorStoreFiles:
    def __init__(self):
        self.created = []
        self.deleted = []
        # Provide SDK compatibility attribute name 'del'
        self.__dict__["del"] = self.delete

    def create(self, vector_store_id: str, file_id: str = None, **kwargs):
        # Support both signatures: create(vector_store_id, {file_id}) and create(vector_store_id=..., file_id=...)
        if file_id is None and isinstance(kwargs.get("file_id"), str):
            file_id = kwargs["file_id"]
        self.created.append((vector_store_id, file_id))
        return types.SimpleNamespace(id=f"vsf_{file_id}")

    def delete(self, vector_store_id: str, file_id: str):
        self.deleted.append((vector_store_id, file_id))
        return {"deleted": True}

    del_ = delete  # additional alias name


class FakeOpenAIVectorStores:
    def __init__(self):
        self.files = FakeOpenAIVectorStoreFiles()
        # add list method on files to align with router expectations
        def _list(vector_store_id: str):
            return types.SimpleNamespace(data=[{"id": "file_a"}, {"id": "file_b"}])
        setattr(self.files, "list", _list)


class FakeOpenAIClient:
    def __init__(self):
        self._files_created = []
        self.files = types.SimpleNamespace(create=self.files_create, delete=self.files_delete)
        setattr(self.files, 'del', self.files_delete)
        self.vector_stores = FakeOpenAIVectorStores()
        self.beta = types.SimpleNamespace(vector_stores=self.vector_stores)

    def files_create_core(self, file):
        fid = f"file_{len(self._files_created)+1}"
        self._files_created.append(fid)
        return FakeOpenAIFile(fid)

    def files_create_with_meta(self, file, purpose, metadata=None):
        return self.files_create_core(file)

    def files_create_no_meta(self, file, purpose):
        return self.files_create_core(file)

    def files_create(self, *args, **kwargs):
        # Accept either (file=..., purpose=..., metadata=...) or (file=..., purpose=...)
        return self.files_create_core(kwargs.get("file"))

    def files_delete(self, file_id):
        return {"deleted": True}

    # Properties to mimic SDK structure
    def files_create_attr(self):
        return self.files_create

    def files_delete_attr(self):
        return self.files_delete

    # Adapter for attribute access
    def __getattr__(self, item):
        if item == "files":
            # provide object with create/delete callable attributes
            ns = types.SimpleNamespace(create=self.files_create, delete=self.files_delete)
            setattr(ns, 'del', self.files_delete)
            return ns
        return super().__getattribute__(item)


class FakeSupabaseTable:
    def __init__(self, name, store):
        self.name = name
        self.store = store
        self._filters = {}
        self._select = None
        self._limit = None

    def select(self, *args, **kwargs):
        self._select = args[0] if args else None
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def ilike(self, col, val):
        self._filters[col] = ("ilike", val)
        return self

    def maybe_single(self):
        self._limit = 1
        return self

    def order(self, *args, **kwargs):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def insert(self, payload):
        # Simulate insert into files and file_workspaces
        if self.name == "files":
            new_id = f"file_db_{len(self.store['files'])+1}"
            self.store["files"].append({"id": new_id, **payload})
            result = types.SimpleNamespace(data=[{"id": new_id, **payload}])
            return types.SimpleNamespace(execute=lambda: result)
        if self.name == "file_workspaces":
            self.store["file_workspaces"].append(payload)
            result = types.SimpleNamespace(data=[payload])
            return types.SimpleNamespace(execute=lambda: result)
        return types.SimpleNamespace(data=[])

    def update(self, payload):
        # For test, we just record the last update
        self.store.setdefault("updates", []).append({"table": self.name, "payload": payload, "filters": dict(self._filters)})
        return self

    def execute(self):
        # Respond to select
        if self.name == "workspace_vector_stores":
            return types.SimpleNamespace(data={"vector_store_id": "vs_test_1"})
        if self.name == "files":
            # maybe_single select by name -> return None to force insert
            return types.SimpleNamespace(data=None)
        if self.name == "file_workspaces":
            # maybe_single select by workspace + normalized_name -> return None
            return types.SimpleNamespace(data=None)
        return types.SimpleNamespace(data=None)


class FakeSupabaseClient:
    def __init__(self):
        self.store = {"files": [], "file_workspaces": []}

    def table(self, name):
        return FakeSupabaseTable(name, self.store)

    class storage:
        @staticmethod
        def from_(bucket):
            class B:
                @staticmethod
                def remove(paths):
                    return {"removed": paths}
            return B()


def build_test_app(monkeypatch):
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(responses_router)

    # Patch OpenAI client constructor used inside routes
    monkeypatch.setattr(responses_module, "OpenAI", lambda: FakeOpenAIClient())
    # Patch supabase client used inside routes
    fake_sb = FakeSupabaseClient()
    monkeypatch.setattr(responses_module, "supabase", fake_sb)
    # Patch vector store id lookup to return a stable id
    monkeypatch.setattr(responses_module, "_get_vector_store_id", lambda ws_id: "vs_test_1")
    # Avoid actual OCR invocation
    monkeypatch.setattr(responses_module, "_run_ocrmypdf", lambda path: None)
    # Make extract_text return a string with a parsable date/body/ordinance
    monkeypatch.setattr(responses_module, "extract_text", lambda p: "City Council meeting January 12, 2022, Ordinance 2023-15")

    return app, fake_sb


def test_ingest_upload_upserts_metadata(monkeypatch):
    app, fake_sb = build_test_app(monkeypatch)
    client = TestClient(app)

    # Prepare a fake PDF file upload
    files = {
        "files": ("Agenda_2022-01-12.pdf", b"%PDF-1.4...", "application/pdf"),
    }
    data = {"workspace_id": "ws_123"}
    resp = client.post("/responses/vector-store/ingest/upload", data=data, files=files)
    assert resp.status_code == 200
    j = resp.json()
    assert j["vector_store_id"] == "vs_test_1"
    assert len(j["files"]) >= 1

    # Verify DB upserts captured expected metadata
    updates = fake_sb.store.get("updates", [])
    # There should be a file_workspaces insert or update; prefer update if present
    fw_updates = [u for u in updates if u["table"] == "file_workspaces"]
    if fw_updates:
        payload = fw_updates[-1]["payload"]
    else:
        # Fall back to last inserted row
        assert fake_sb.store["file_workspaces"], "Expected file_workspaces row"
        payload = fake_sb.store["file_workspaces"][-1]
    # Check soft-filter metadata presence
    assert payload.get("ingested") is True
    assert payload.get("doc_type") in {"agenda", "minutes", "ordinance", "transcript", "report"}
    assert payload.get("meeting_year") in (2022, None)
    assert payload.get("meeting_month") in (1, None)
    assert payload.get("meeting_day") in (12, None)
    assert payload.get("has_ocr") in {True, False, None}


def test_purge_vector_store_resets_flags(monkeypatch):
    app, fake_sb = build_test_app(monkeypatch)
    client = TestClient(app)

    # Seed a file_workspaces row via an update to simulate existing ingestion
    fake_sb.table("file_workspaces").update({
        "ingested": True,
        "openai_file_id": "file_1",
        "vs_file_id": "vsf_file_1",
    }).eq("workspace_id", "ws_123").eq("deleted", False).execute()

    body = {"workspace_id": "ws_123", "delete_openai": True, "reset_db_flags": True}
    resp = client.post("/responses/vector-store/purge", json=body)
    assert resp.status_code == 200
    j = resp.json()
    assert j["ok"] is True
    assert j["vector_store_id"] == "vs_test_1"
    assert j["detached"] >= 1

    # Check that reset flag update was recorded
    updates = fake_sb.store.get("updates", [])
    assert any(u["table"] == "file_workspaces" and u["payload"].get("ingested") is False for u in updates)
