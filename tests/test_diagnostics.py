from unittest.mock import Mock, patch

import pytest


@pytest.mark.parametrize("valid_pin", [True, False])
def test_ssh_pin_is_sha256_and_connection_closes(tmp_path, valid_pin):
    import hashlib
    import base64

    from kajovo.core.diagnostics.ssh import collect_ssh_diagnostics
    key = b"test public key"
    fingerprint = base64.b64encode(hashlib.sha256(key).digest()).decode().rstrip("=")
    client = Mock()
    client.get_transport.return_value.get_remote_server_key.return_value.asbytes.return_value = key
    client.exec_command.return_value = (Mock(), Mock(read=lambda: b"result"), Mock(read=lambda: b""))
    with patch("kajovo.core.diagnostics.ssh.paramiko.SSHClient", return_value=client):
        if valid_pin:
            _, files = collect_ssh_diagnostics(str(tmp_path), "host", "user", "", "", pin="SHA256:" + fingerprint, pin_required=True)
            assert len(files) == 1
        else:
            with pytest.raises(RuntimeError, match="SHA256"):
                collect_ssh_diagnostics(str(tmp_path), "host", "user", "", "", pin=fingerprint.swapcase(), pin_required=True)
            client.exec_command.assert_not_called()
    client.close.assert_called_once()


def test_ssh_repair_executes_script_on_remote_host():
    from types import SimpleNamespace
    from kajovo.core.diagnostics.ssh import execute_ssh_repair
    cfg = SimpleNamespace(ssh_host="host", ssh_user="user", ssh_key="", ssh_password="",
                          ssh_pin="", ssh_pin_required=False)
    client = Mock()
    stdin, stdout, stderr = Mock(), Mock(), Mock()
    stdout.read.return_value = b"remote result"
    stdout.channel.recv_exit_status.return_value = 0
    client.exec_command.return_value = stdin, stdout, stderr
    with patch("kajovo.core.diagnostics.ssh.paramiko.SSHClient", return_value=client), patch(
        "subprocess.run"
    ) as local:
        result = execute_ssh_repair(b"echo checked\n", cfg)
    local.assert_not_called()
    client.exec_command.assert_called_once_with("sh -s 2>&1", timeout=120)
    stdin.write.assert_called_once_with(b"echo checked\n")
    stdin.channel.shutdown_write.assert_called_once()
    client.close.assert_called_once()
    assert result.stdout == "remote result" and result.returncode == 0
