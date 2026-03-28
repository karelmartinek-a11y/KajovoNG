from __future__ import annotations

import base64
import os
from pathlib import Path

try:
    import keyring
    from keyring.errors import KeyringError
except Exception:  # pragma: no cover
    keyring = None

    class KeyringError(Exception):
        pass

from platformdirs import user_config_dir

from kajovospend.app.constants import APP_NAME


class SecretStoreError(RuntimeError):
    pass


class SecretStore:
    def __init__(self, service_name: str = APP_NAME) -> None:
        self.service_name = service_name
        self.backup_path = Path(user_config_dir(APP_NAME, 'OpenAI')) / '.openai_key'

    def set_openai_key(self, key: str) -> None:
        if keyring is not None:
            try:
                keyring.set_password(self.service_name, 'openai_api_key', key)
                return
            except KeyringError:
                pass
        self.backup_path.parent.mkdir(parents=True, exist_ok=True)
        encoded = base64.b64encode(key.encode('utf-8')).decode('ascii')
        self.backup_path.write_text(encoded, encoding='utf-8')
        try:
            os.chmod(self.backup_path, 0o600)
        except OSError:
            pass

    def get_openai_key(self) -> str:
        if keyring is not None:
            try:
                value = keyring.get_password(self.service_name, 'openai_api_key')
                if value:
                    return value
            except KeyringError:
                pass
        if not self.backup_path.exists():
            return ''
        try:
            return base64.b64decode(self.backup_path.read_text(encoding='utf-8')).decode('utf-8')
        except Exception as exc:
            raise SecretStoreError(f'Nepodařilo se načíst API key: {exc}') from exc

    def delete_openai_key(self) -> None:
        if keyring is not None:
            try:
                current = keyring.get_password(self.service_name, 'openai_api_key')
                if current is not None:
                    keyring.delete_password(self.service_name, 'openai_api_key')
            except KeyringError:
                pass
        if self.backup_path.exists():
            self.backup_path.unlink()
