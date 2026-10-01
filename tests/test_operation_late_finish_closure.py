"""Ukončení rychlého workeru před registrací úklidu nesmí zablokovat panel."""

from kajovo.studio.operations import Task
from kajovo.studio.versions import VersionsPage
from test_settings_http_closure import fixture


def test_git_finished_before_return_releases_panel(qtbot, monkeypatch, tmp_path):
    settings, context, _, _ = fixture(qtbot, monkeypatch, tmp_path)
    page = VersionsPage(context, settings)
    qtbot.addWidget(page)
    root = tmp_path / "projekt"
    root.mkdir()
    page.path.setText(str(root))
    original = Task.start

    def finish_before_return(worker):
        original(worker)
        assert worker.wait(10000), "Skutečný Git worker musí skončit."

    monkeypatch.setattr(Task, "start", finish_before_return)
    record = page.execute("Založení", lambda service: service.init())
    qtbot.waitUntil(lambda: bool(record.terminal), timeout=10000)
    assert (root / ".git").is_dir()
    assert record.terminal == "completed"
    assert not page.busy, "Dokončený worker nesmí trvale zablokovat další Git akci."
