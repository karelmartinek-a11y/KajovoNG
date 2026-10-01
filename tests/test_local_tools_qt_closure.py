"""Git a UTF-8 provozní UI nad výhradně dočasnými soubory."""

import threading
import zipfile

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QPushButton

from kajovo.core.project_git import ProjectGit
from kajovo.studio.converter import ConverterWindow
from kajovo.studio.versions import VersionsPage
from test_settings_http_closure import fixture


def finished(qtbot, operations):
    qtbot.waitUntil(lambda: bool(operations.records) and not operations.active, timeout=30000)
    record = list(operations.records.values())[-1]
    qtbot.addWidget(record.dialog)
    record.dialog.close()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    return record


def click(qtbot, page, name):
    qtbot.waitUntil(lambda: not page.busy, timeout=30000)
    count = len(page.context.operations.records)
    page.findChild(QPushButton, name).click()
    assert len(page.context.operations.records) == count + 1


def test_git_real_ui_init_edit_hash_guard_and_symlink(qtbot, monkeypatch, tmp_path):
    settings_page, context, _, _ = fixture(qtbot, monkeypatch, tmp_path)
    page = VersionsPage(context, settings_page)
    qtbot.addWidget(page)
    root = tmp_path / 'český projekt'
    root.mkdir()
    original = 'Původní bytes.\n'.encode()
    target = root / 'soubor.txt'
    target.write_bytes(original)
    page.path.setText(str(root))
    click(qtbot, page, 'git.init')
    assert finished(qtbot, context.operations).terminal == 'completed'
    assert (root / '.git').is_dir() and not page.busy
    service = ProjectGit(root)
    service.command('config','user.name','Offline test')
    service.command('config','user.email','test@example.invalid')
    service.command('add','.')
    service.command('commit','-m','Výchozí stav')
    click(qtbot, page, 'git.refresh')
    finished(qtbot, context.operations)
    selected = next(i for i in range(page.files.count()) if page.files.item(i).text() == 'soubor.txt')
    page.files.setCurrentRow(selected)
    click(qtbot, page, 'git.file.open')
    assert finished(qtbot, context.operations).terminal == 'completed'
    assert page.editor.toPlainText().encode() == original.rstrip(b'\n') + b'\n'
    page.editor.setPlainText('Upravené bytes.\n')
    click(qtbot, page, 'git.file.save')
    assert finished(qtbot, context.operations).terminal == 'completed'
    assert target.read_bytes() == 'Upravené bytes.\n'.encode()
    target.write_bytes(b'external change\n')
    page.editor.setPlainText('Nesmí přepsat')
    click(qtbot, page, 'git.file.save')
    assert finished(qtbot, context.operations).terminal == 'failed'
    assert target.read_bytes() == b'external change\n'
    external = tmp_path / 'foreign.txt'
    external.write_bytes(b'foreign')
    (root / 'symlink.txt').symlink_to(external)
    with pytest.raises(ValueError):
        service.read_file('symlink.txt')
    assert external.read_bytes() == b'foreign'


def test_git_accountless_path_aba_rejects_late_snapshot(qtbot, monkeypatch, tmp_path):
    settings_page, context, _, _ = fixture(qtbot, monkeypatch, tmp_path)
    page = VersionsPage(context, settings_page)
    qtbot.addWidget(page)
    root, other = tmp_path / 'A', tmp_path / 'B'
    root.mkdir()
    other.mkdir()
    entered, release = threading.Event(), threading.Event()
    original = ProjectGit.snapshot
    def snapshot(service):
        result = original(service)
        entered.set()
        assert release.wait(20), 'Git bariéra nebyla uvolněna'
        return result
    monkeypatch.setattr(ProjectGit, 'snapshot', snapshot)
    page.path.setText(str(root))
    click(qtbot, page, 'git.refresh')
    qtbot.waitUntil(entered.is_set)
    page.path.setText(str(other))
    page.path.setText(str(root))
    page.status.setPlainText('Nový výběr čeká na načtení.')
    release.set()
    record = finished(qtbot, context.operations)
    assert page.status.toPlainText() == 'Nový výběr čeká na načtení.'
    assert record.terminal == 'completed'


@pytest.mark.parametrize('fault', [False, True])
def test_utf8_native_action_backup_bytes_symlink_and_failure(qtbot, monkeypatch, tmp_path, fault):
    page = ConverterWindow()
    qtbot.addWidget(page)
    source, backup = tmp_path / 'zdroj', tmp_path / 'záloha'
    source.mkdir()
    backup.mkdir()
    raw = b'\xef\xbb\xbf' + 'Příliš žluťoučký kůň\r\n'.encode()
    target = source / 'český soubor.txt'
    target.write_bytes(raw)
    outside = tmp_path / 'outside.txt'
    outside.write_bytes(raw)
    (source / 'link.txt').symlink_to(outside)
    page.paths[0].setText(str(source))
    page.backup.setText(str(backup))
    if fault:
        def fail(*args):
            raise OSError('syntetické selhání zálohy')
        monkeypatch.setattr('utf8nobom.app.copy_directory_for_backup', fail)
    page.start_button.click()
    page.start_button.click()
    record = finished(qtbot, page.operations)
    assert len(page.operations.records) == 1
    assert record.terminal == ('failed' if fault else 'completed')
    assert outside.read_bytes() == raw
    if fault:
        assert target.read_bytes() == raw and not page.result.toPlainText()
    else:
        assert target.read_bytes() == raw[3:]
        archives = list(backup.rglob('*.zip'))
        assert len(archives) == 1
        with zipfile.ZipFile(archives[0]) as archive:
            assert archive.read('český soubor.txt') == raw
        assert 'Hotovo.' in page.result.toPlainText()
        assert any(event.total and event.completed == event.total for event in record.events)


def test_git_milestone_restore_and_local_remote_real_ui(qtbot, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QDialog
    from kajovo.studio.resources import ValueDialog
    settings_page, context, _, _ = fixture(qtbot,monkeypatch,tmp_path)
    page = VersionsPage(context,settings_page)
    qtbot.addWidget(page)
    root = tmp_path/'project'
    root.mkdir()
    target = root/'obsah.txt'
    target.write_bytes('Původní český obsah\n'.encode())
    page.path.setText(str(root))
    click(qtbot, page,'git.init')
    finished(qtbot,context.operations)
    service = ProjectGit(root)
    service.command('config','user.name','Offline test')
    service.command('config','user.email','test@example.invalid')
    service.command('add','.')
    service.command('commit','-m','base')
    def accept(dialog):
        dialog.value = 'milnik'
        return QDialog.Accepted
    monkeypatch.setattr(ValueDialog,'exec',accept)
    monkeypatch.setattr('kajovo.studio.versions.confirm',lambda *a:True)
    click(qtbot, page,'git.tag.create')
    assert finished(qtbot,context.operations).terminal == 'completed'
    assert page.tags.count() == 1
    target.write_bytes(b'changed\n')
    service.command('add','obsah.txt')
    service.command('commit','-m','change')
    page.tags.setCurrentRow(0)
    click(qtbot, page,'git.tag.restore')
    assert finished(qtbot,context.operations).terminal == 'completed'
    assert target.read_bytes() == 'Původní český obsah\n'.encode()
    service.command('add','obsah.txt')
    service.command('commit','-m','restore')
    remote = tmp_path/'remote.git'
    service.command('init','--bare',str(remote))
    page.remote.setText(str(remote))
    click(qtbot, page,'git.remote.save')
    finished(qtbot,context.operations)
    for operation in ['git.push','git.pull']:
        click(qtbot, page,operation)
        assert finished(qtbot,context.operations).terminal == 'completed'
    assert ProjectGit(remote).command('show','HEAD:obsah.txt').stdout.encode() == target.read_bytes()
    page.tags.setCurrentRow(0)
    click(qtbot, page,'git.tag.delete')
    assert finished(qtbot,context.operations).terminal == 'completed'
    assert page.tags.count() == 0 and target.read_bytes() == 'Původní český obsah\n'.encode()


def test_converter_close_during_backup_waits_for_safe_finish(qtbot, monkeypatch, tmp_path):
    import utf8nobom.app as app
    page = ConverterWindow()
    qtbot.addWidget(page)
    page.show()
    source,backup = tmp_path/'source',tmp_path/'backup'
    source.mkdir()
    backup.mkdir()
    raw = b'\xef\xbb\xbfOriginal\n'
    target = source/'original.txt'
    target.write_bytes(raw)
    page.paths[0].setText(str(source))
    page.backup.setText(str(backup))
    entered, release = threading.Event(),threading.Event()
    original = app.copy_directory_for_backup
    def boundary(*args,**kwargs):
        entered.set()
        assert release.wait(20), 'Backup bariéra nebyla uvolněna'
        return original(*args,**kwargs)
    monkeypatch.setattr(app,'copy_directory_for_backup',boundary)
    page.start_button.click()
    qtbot.waitUntil(entered.is_set)
    page.close()
    visible = page.isVisible()
    release.set()
    assert visible and target.read_bytes() == raw
    assert finished(qtbot,page.operations).terminal == 'completed'
    assert target.read_bytes() == raw[3:]
    page.close()
    assert not page.isVisible()
