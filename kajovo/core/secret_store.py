from __future__ import annotations

import os
from typing import Optional

SERVICE_NAME = "kajovo"


def persist_api_key(value: str) -> bool:
    """Uloží API klíč do uživatelského prostředí Windows bez argumentu procesu."""
    if os.name != "nt":
        return False
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            if value:
                winreg.SetValueEx(key, "OPENAI_API_KEY", 0, winreg.REG_SZ, value)
            else:
                try:
                    winreg.DeleteValue(key, "OPENAI_API_KEY")
                except FileNotFoundError:
                    pass
        return True
    except OSError:
        return False


def _env_name(key: str) -> str:
    return f"KAJOVO_SECRET_{key.upper()}"


def set_secret(key: str, value: str) -> bool:
    value = value or ""
    env_name = _env_name(key)
    os.environ.pop(env_name, None)
    try:
        import keyring  # type: ignore

        if value:
            keyring.set_password(SERVICE_NAME, key, value)
        else:
            try:
                keyring.delete_password(SERVICE_NAME, key)
            except keyring.errors.PasswordDeleteError:
                pass
        return True
    except Exception:
        if value:
            os.environ[env_name] = value
        else:
            os.environ.pop(env_name, None)
        return False


def get_secret(key: str) -> Optional[str]:
    env_name = _env_name(key)
    if env_name in os.environ:
        return os.environ[env_name] or None
    try:
        import keyring  # type: ignore

        value = keyring.get_password(SERVICE_NAME, key)
        if value:
            return value
    except Exception:
        pass
    value = os.environ.get(_env_name(key), "")
    return value or None
