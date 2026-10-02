"""Files/vector stores přes skutečné UI akce, QThread a HTTP hranici."""

import copy
from native_provider_fixtures import native_fixture
import json
import threading

import pytest
import requests
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QPushButton

from kajovo.core.config import AppSettings
from kajovo.core.openai_client import OpenAIClient
from kajovo.studio.context import StudioContext
from kajovo.studio.operations import Operations
from kajovo.studio.resources import ResourcesPage, ValueDialog
from test_runtime_end_to_end import child


class ResourcesHttp:
    def __init__(self, root):
        self.root = root
        path = root / "remote-resources.json"
        self.state = json.loads(path.read_text()) if path.exists() else {"files": {}, "stores": {}, "members": {}}
        self.calls = []
        self.block = ""
        self.entered, self.release = threading.Event(), threading.Event()
        self.fail = ""

    def request(self, method, url, **kwargs):
        path = url.split("/v1", 1)[1].split("?", 1)[0]
        body = copy.deepcopy(kwargs.get("json"))
        row = {"method": method, "path": path, "url": url, "body": body}
        self.calls.append(row)
        if path == self.block:
            self.entered.set()
            assert self.release.wait(20), "Chybí uvolnění řízené HTTP bariéry"
        code, raw = 200, None
        if (method, path) == ("GET", "/files"):
            value = {"data": list(self.state["files"].values()), "has_more": False}
        elif (method, path) == ("POST", "/files"):
            assert kwargs["data"] == {"purpose": "user_data"}
            assert "Content-Type" not in kwargs["headers"]
            name, stream = kwargs["files"]["file"]
            binary = stream.read()
            row.update(filename=name, multipart_bytes=list(binary), purpose=kwargs["data"]["purpose"])
            identifier = f"file_{len(self.state['files']) + 1}"
            value = {"id": identifier, "filename": name, "bytes": len(binary), "purpose": "user_data"}
            self.state["files"][identifier] = {**value, "content": binary.decode()}
        elif (method, path) == ("GET", "/vector_stores"):
            value = {"data": list(self.state["stores"].values()), "has_more": False}
        elif (method, path) == ("POST", "/vector_stores"):
            assert body == {"name": "Testovací úložiště"}
            value = {"id": "vs_one", "name": body["name"], "status": "completed", "file_counts": {"completed": 0, "in_progress": 0, "failed": 0, "cancelled": 0, "total": 0}}
            self.state["stores"]["vs_one"] = value
        elif path.startswith("/vector_stores/vs_one/files"):
            if method == "POST" and path.endswith("/files"):
                assert body == {"file_id": "file_1"}
                value = {"id": "file_1", "vector_store_id": "vs_one", "status": "in_progress", "attributes": {}}
                self.state["members"]["file_1"] = value
                self.state["stores"]["vs_one"]["file_counts"] = {"completed": 0, "in_progress": 1, "failed": 0, "cancelled": 0, "total": 1}
            elif method == "GET" and path.endswith("/files"):
                value = {"data": list(self.state["members"].values()), "has_more": False}
            elif method == "DELETE":
                value = {"id": path.rsplit("/", 1)[1], "deleted": True}
                self.state["members"].pop(value["id"], None)
            elif method == "POST":
                assert body == {"attributes": {"language": "cs"}}
                value = self.state["members"]["file_1"]
                value.update(body)
            else:
                value = self.state["members"]["file_1"]
        elif path == "/vector_stores/vs_one":
            value = self.state["stores"]["vs_one"]
            if method == "DELETE":
                self.state["stores"].pop("vs_one")
                value = {"id": "vs_one", "deleted": True}
        elif path.startswith("/files/file_"):
            identifier = path.split("/")[2]
            value = self.state["files"][identifier]
            if method == "DELETE":
                if identifier == self.fail:
                    code, value = 403, {"error": {"message": "Syntetické odmítnutí"}}
                else:
                    self.state["files"].pop(identifier)
                    value = {"id": identifier, "deleted": True}
            elif path.endswith("/content"):
                raw = value["content"].encode()
        elif path == "/models":
            value = {"data": [{"id": "gpt-4o-mini"}], "has_more": False}
        else:
            raise AssertionError(f"Neočekávaný HTTP {method} {path}")
        (self.root / "remote-resources.json").write_text(json.dumps(self.state))
        with (self.root / "resources-http.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        if path == self.fail and method == "POST":
            raise requests.Timeout("Syntetický timeout po přijetí")
        response = requests.Response()
        response.status_code = code
        response.headers["Content-Type"] = "application/json" if raw is None else "application/octet-stream"
        response._content = json.dumps(native_fixture(method, path, value, kwargs.get("json") or kwargs.get("data"))).encode() if raw is None else raw
        return response


def client_for(transport):
    client = OpenAIClient("synthetic", base_url="https://offline.invalid/v1")
    client._sdk = None
    client.session.request = transport.request
    return client


def page_fixture(qtbot, tmp_path, transport):
    operations = Operations(None)
    context = StudioContext(AppSettings(cache_dir=str(tmp_path / "cache"), log_dir=str(tmp_path / "LOG")), operations,
                            api_key="account-A", client_factory=lambda *a, **k: client_for(transport))
    page = ResourcesPage(context)
    operations.setParent(page)
    qtbot.addWidget(page)
    return page, context


def finish(qtbot, page):
    qtbot.waitUntil(lambda: not page.busy and not page.context.operations.active, timeout=30000)
    record = list(page.context.operations.records.values())[-1]
    qtbot.addWidget(record.dialog)
    record.dialog.close()
    return record


def click(page, name):
    page.findChild(QPushButton, name).click()


def test_resources_upload_store_indexing_delete_ui_and_new_process(qtbot, monkeypatch, tmp_path):
    transport = ResourcesHttp(tmp_path)
    page, context = page_fixture(qtbot, tmp_path, transport)
    source = tmp_path / "český soubor.txt"
    source.write_bytes("Přesné multipart bytes\n".encode())
    monkeypatch.setattr("kajovo.studio.resources.get_open_file_names", lambda *a: ([str(source)], ""))
    click(page, "resources.files.create")
    assert finish(qtbot, page).terminal == "completed"
    assert page.lists["files"].item(0).data(Qt.UserRole) == "file_1"
    assert bytes(next(r for r in transport.calls if r["method"] == "POST")["multipart_bytes"]) == source.read_bytes()
    page.lists["files"].item(0).setSelected(True)
    click(page, "resources.files.attach")
    assert context.files == ["file_1"]
    before_detach = len(transport.calls)
    click(page, "resources.files.detach")
    assert context.files == [] and len(transport.calls) == before_detach
    click(page, "resources.files.attach")
    assert context.files == ["file_1"]

    def accept(dialog):
        dialog.value = "Testovací úložiště"
        return QDialog.Accepted
    monkeypatch.setattr(ValueDialog, "exec", accept)
    click(page, "resources.stores.create")
    assert finish(qtbot, page).terminal == "completed"
    page.lists["stores"].setCurrentRow(0)
    page.lists["files"].item(0).setSelected(True)
    click(page, "resources.store.add_selected")
    assert finish(qtbot, page).terminal == "completed"
    assert page.store_files.item(0).data(Qt.UserRole + 1)["status"] == "in_progress"
    client = client_for(transport)
    payload = {"model": "gpt-4o-mini", "input": "Dotaz", "tools": [{"type": "file_search", "vector_store_ids": ["vs_one"]}]}
    with pytest.raises(ValueError, match="kompletně"):
        client.validate_resources(payload)
    transport.state["stores"]["vs_one"]["file_counts"].update(completed=1, in_progress=0)
    transport.state["members"]["file_1"]["status"] = "completed"
    click(page, "resources.store.files")
    finish(qtbot, page)
    assert page.store_files.item(0).data(Qt.UserRole + 1)["status"] == "completed"
    client.validate_resources(payload)
    # Navazující QA skutečně spotřebuje vybrané Files i store přes produkční dispatcher.
    from kajovo.studio.workbench import Workbench
    from test_qa_qfile_http_closure import WorkflowHttp, qa_value, http_client, settle
    qa_transport = WorkflowHttp(tmp_path, answer=qa_value("file_1"))
    class Router:
        def request(self, method, url, **kwargs):
            return (qa_transport if "/responses" in url else transport).request(method, url, **kwargs)
    router = Router()
    context.stores = ["vs_one"]
    context.models = ["gpt-4o-mini"]
    context.client_factory = lambda *a, **k: http_client(router)
    monkeypatch.setattr("kajovo.core.runs.executor.OpenAIClient", lambda *a, **k: http_client(router))
    workbench = Workbench(context)
    qtbot.addWidget(workbench)
    workbench.apply_state({"project":"Zdroje QA", "prompt":"Dolož odpověď z připojeného souboru", "mode":"QA", "model":"gpt-4o-mini", "out_dir":str(tmp_path / "OUT"), "attached_file_ids":["file_1"], "attached_vector_store_ids":["vs_one"]})
    workbench.start_button.click()
    settle(qtbot, context.operations, workbench)
    assert workbench.result.value and workbench.result.value["status"] == "completed", [(r.error,r.result) for r in context.operations.records.values()]
    qa_request = next(r["body"] for r in qa_transport.calls if r["path"] == "/responses")
    assert qa_request["tools"] == [{"type":"file_search", "vector_store_ids":["vs_one"]}]
    assert "file_1" in json.dumps(qa_request["input"])
    page.store_files.setCurrentRow(0)
    def attributes(dialog):
        dialog.value = {"language":"cs"}
        return QDialog.Accepted
    monkeypatch.setattr(ValueDialog, "exec", attributes)
    click(page, "resources.store.attributes")
    assert finish(qtbot, page).terminal == "completed"
    assert transport.state["members"]["file_1"]["attributes"] == {"language":"cs"}
    assert client.retrieve_file("file_1")["bytes"] == len(source.read_bytes())
    assert client.file_content("file_1") == source.read_bytes()
    child("from pathlib import Path; import sys; from test_resources_http_closure import resource_restart; resource_restart(Path(sys.argv[1]))", tmp_path)
    monkeypatch.setattr("kajovo.studio.resources.confirm", lambda *a: True)
    page.store_files.setCurrentRow(0)
    click(page, "resources.store.remove")
    finish(qtbot, page)
    assert page.store_files.count() == 0 and "file_1" in transport.state["files"]
    page.lists["files"].item(0).setSelected(True)
    click(page, "resources.files.delete")
    finish(qtbot, page)
    assert page.lists["files"].count() == 0 and context.files == []
    page.lists["stores"].setCurrentRow(0)
    click(page, "resources.stores.delete")
    assert finish(qtbot, page).terminal == "completed"
    assert page.lists["stores"].count() == 0 and context.stores == []


def resource_restart(root):
    transport = ResourcesHttp(root)
    client = client_for(transport)
    assert client.list_files()[0]["id"] == "file_1"
    assert client.list_vector_store_files("vs_one")[0]["status"] == "completed"
    assert client.file_content("file_1") == "Přesné multipart bytes\n".encode()
    assert all(row["method"] == "GET" for row in transport.calls)


@pytest.mark.parametrize("operation", ["resources", "catalog"])
def test_account_aba_rejects_late_http_callback(qtbot, tmp_path, operation):
    transport = ResourcesHttp(tmp_path)
    transport.state["files"]["file_old"] = {"id": "file_old", "filename": "Původní účet"}
    transport.block = "/files" if operation == "resources" else "/models"
    page, context = page_fixture(qtbot, tmp_path, transport)
    if operation == "resources":
        click(page, "resources.files.refresh")
    else:
        context.refresh_models()
    qtbot.waitUntil(transport.entered.is_set)
    context.set_key("account-B")
    context.set_key("account-A")
    transport.release.set()
    qtbot.waitUntil(lambda: not context.operations.active and not page.busy)
    for record in context.operations.records.values():
        qtbot.addWidget(record.dialog)
        record.dialog.close()
    assert page.lists["files"].count() == 0
    assert context.models == []
    assert not (tmp_path / "cache" / "model_catalog.json").exists()


@pytest.mark.parametrize("path", ["/files", "/vector_stores"])
def test_uncertain_resource_create_is_one_submit_then_safe_refresh(qtbot, monkeypatch, tmp_path, path):
    transport = ResourcesHttp(tmp_path)
    transport.fail = path
    page, _ = page_fixture(qtbot, tmp_path, transport)
    source = tmp_path / "input.txt"
    source.write_text("Původní obsah")
    monkeypatch.setattr("kajovo.studio.resources.get_open_file_names", lambda *a: ([str(source)], ""))
    def accept(dialog):
        dialog.value = "Testovací úložiště"
        return QDialog.Accepted
    monkeypatch.setattr(ValueDialog, "exec", accept)
    kind = "files" if path == "/files" else "stores"
    click(page, f"resources.{kind}.create")
    assert finish(qtbot, page).terminal == "submission_unknown"
    click(page, f"resources.{kind}.refresh")
    finish(qtbot, page)
    assert page.lists[kind].count() == 1
    assert sum(r["method"] == "POST" and r["path"] == path for r in transport.calls) == 1


def test_resources_double_click_upload_is_single_worker_and_exact_bytes(qtbot, monkeypatch, tmp_path):
    transport = ResourcesHttp(tmp_path)
    transport.block = '/files'
    page, _ = page_fixture(qtbot, tmp_path, transport)
    source = tmp_path / 'input.txt'
    source.write_bytes(b'Exact input\n')
    monkeypatch.setattr('kajovo.studio.resources.get_open_file_names', lambda *a: ([str(source)], ''))
    click(page, 'resources.files.create')
    qtbot.waitUntil(transport.entered.is_set)
    click(page, 'resources.files.create')
    assert len(page.context.operations.records) == 1
    transport.release.set()
    assert finish(qtbot, page).terminal == 'completed'
    posts = [row for row in transport.calls if row['method'] == 'POST']
    assert len(posts) == 1 and bytes(posts[0]['multipart_bytes']) == source.read_bytes()
    assert page.lists['files'].count() == 1
