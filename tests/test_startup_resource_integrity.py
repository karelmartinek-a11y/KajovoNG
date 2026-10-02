"""CheckOnly musí ověřit konkrétní obsah prostředků, ne jen jejich existenci."""

from pathlib import Path
from unittest.mock import Mock

import pytest

from test_startup_sanity import launcher as launcher


@pytest.mark.parametrize("name,content", [
    ("studio-symbol.png", b"neni obraz"),
    ("montserrat_regular.ttf", b"neni font"),
    ("montserrat_regular.ttf", b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"0" * 64 + b"\nsize 263192\n"),
    ("orchestration/contracts/local/BATCH_MANIFEST_V4.schema.json", b'{"type": "object"}'),
    ("orchestration/contracts/local/RUN_CONFIG_V2.schema.json", b'{"type": 19}'),
    ("orchestration/contracts/wire/FILE_CONTENT_V1.schema.json", b'{"type":"object","type":"object"}'),
])
def test_check_only_rejects_corrupt_required_resource(launcher, monkeypatch, name, content):
    from kajovo.core.resources import resource_path
    path = launcher.ROOT / Path(name).name
    path.write_bytes(content)
    monkeypatch.setattr("kajovo.core.resources.resource_path", lambda value: path if value == name else resource_path(value))
    monkeypatch.setattr(launcher, "missing_requirements", lambda _: [])
    commands = Mock(return_value=0)
    monkeypatch.setattr(launcher, "run", commands)
    assert launcher.prepare() == 1
    assert path.read_bytes() == content
    assert not any(call.args[:2] == ("-m", "pip") and "install" in call.args for call in commands.mock_calls)


def test_check_only_rejects_missing_nonanchor_resource(launcher, monkeypatch):
    from kajovo.core.resources import resource_path
    name = "orchestration/contracts/local/BATCH_MANIFEST_V4.schema.json"
    monkeypatch.setattr("kajovo.core.resources.resource_path", lambda value: launcher.ROOT / "absent" if value == name else resource_path(value))
    monkeypatch.setattr(launcher, "missing_requirements", lambda _: [])
    monkeypatch.setattr(launcher, "run", lambda *args: 0)
    assert launcher.prepare() == 1


@pytest.mark.parametrize("contents", [None, b"{neplatny json"])
def test_check_only_rejects_missing_or_corrupt_image_policy(launcher, monkeypatch, contents):
    from kajovo.core.orchestration import image_slots
    root = launcher.ROOT / "policy-package"
    (root / "policies").mkdir(parents=True)
    if contents:
        (root / "policies/images.json").write_bytes(contents)
    monkeypatch.setattr(image_slots, "files", lambda package: root)
    monkeypatch.setattr(launcher, "missing_requirements", lambda _: [])
    monkeypatch.setattr(launcher, "run", lambda *args: 0)
    assert launcher.prepare() == 1
